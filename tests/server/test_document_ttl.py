# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""TTL configuration and live-document changes through the public HTTP surface."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openviking_cli.utils.config import get_openviking_config, set_openviking_config
from tests.storage.test_transfer_merge_binding import root_ctx

ROOT = "viking://user/default"
CONFIG = "/api/v1/admin/accounts/default/configuration"


@pytest.fixture(autouse=True)
def restore_config():
    original = get_openviking_config()
    yield
    set_openviking_config(original)


@pytest.fixture
async def ttl_admin_app(app):
    # ASGI test apps intentionally omit the auth lifespan. Match the existing
    # settings tests' admin gate while exercising the real config/storage stack.
    app.state.api_key_manager = SimpleNamespace(
        refresh_accounts_from_store=AsyncMock(),
        refresh_account_users_from_store=AsyncMock(),
        ensure_account_active=lambda account: None,
        get_accounts=lambda: [{"account_id": "default"}],
    )
    return app


@pytest.fixture
async def client(ttl_admin_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ttl_admin_app), base_url="http://testserver"
    ) as client:
        yield client


async def request(client, method, path, **kwargs):
    response = await getattr(client, method)(path, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["result"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node",
    [
        "global",
        "global_default",
        "user_events",
        "peer_events",
        "sessions",
        ROOT + "/memories/events",
        ROOT + "/peers/p1/memories/events",
        ROOT + "/sessions",
    ],
)
async def test_policy_mode_is_required_before_create_or_patch(client, service, node):
    from openviking.config.scope import ConfigScope

    manager = service.runtime_config_manager

    def settings(policy):
        ttl = {"directories": {node: policy}} if node.startswith("viking://") else {node: policy}
        return {"ttl": ttl}

    # A previously saved mode must not make an incomplete new request valid.
    for existing in (False, True):
        if existing:
            await manager.patch_cluster(settings({"mode": "days", "ttl_days": 7}))
            await manager.patch_account("default", settings({"mode": "days", "ttl_days": 7}))
        before = [
            await manager.get_settings(scope)
            for scope in (ConfigScope.cluster(), ConfigScope.account("default"))
        ]
        for policy in ({}, {"ttl_days": 30}, {"ttl_absolute": 4102444800}, {"mode": None}):
            for method, path in (
                ("post", "/api/v1/admin/accounts"),
                ("patch", CONFIG),
                ("patch", "/api/v1/admin/configuration"),
            ):
                body = {"settings": settings(policy)}
                if method == "post":
                    body.update(account_id="missing-mode", admin_user_id="alice")
                response = await getattr(client, method)(path, json=body)
                assert response.status_code == 400, response.text
                error = response.json()["error"]
                assert error["code"] == "INVALID_ARGUMENT"
                assert "mode is required" in error["message"]
        assert before == [
            await manager.get_settings(scope)
            for scope in (ConfigScope.cluster(), ConfigScope.account("default"))
        ]


@pytest.mark.asyncio
async def test_session_api_inherits_root_and_returns_expiry_everywhere(client, service):
    await request(
        client,
        "patch",
        CONFIG,
        json={
            "settings": {
                "ttl": {
                    "global": {"mode": "days", "ttl_days": 30},
                    "directories": {ROOT + "/sessions": {"mode": "days", "ttl_days": 7}},
                }
            }
        },
    )
    created = await request(client, "post", "/api/v1/sessions", json={"session_id": "ttl-api"})
    expiry = created["expires_at"]
    assert expiry
    details = await request(client, "get", "/api/v1/sessions/ttl-api")
    assert details["expires_at"] == expiry
    assert {"ttl_days", "received_at", "ttl_per_file", "ttl_generation"}.isdisjoint(details)
    listed = await request(client, "get", "/api/v1/sessions")
    assert next(row for row in listed if row["session_id"] == "ttl-api")["expires_at"] == expiry
    changed = await request(client, "patch", "/api/v1/sessions/ttl-api/config", json={})
    assert changed["expires_at"] == expiry
    for endpoint in ["context", "tool-results"]:
        response = await client.get("/api/v1/sessions/ttl-api/" + endpoint)
        assert response.status_code == 200, response.text
        assert response.json()["expires_at"] == expiry
    for endpoint, body in [
        ("messages", {"role": "user", "content": "hello"}),
        ("messages/batch", {"messages": [{"role": "assistant", "content": "world"}]}),
    ]:
        result = await request(client, "post", "/api/v1/sessions/ttl-api/" + endpoint, json=body)
        assert result["expires_at"] == expiry
    await request(
        client,
        "patch",
        CONFIG,
        json={
            "settings": {
                "ttl": {
                    "sessions": {"mode": "disabled"},
                    "directories": {ROOT + "/sessions": {"mode": "disabled"}},
                }
            }
        },
    )
    no_ttl = await request(client, "post", "/api/v1/sessions", json={"session_id": "no-ttl"})
    assert "expires_at" in no_ttl and no_ttl["expires_at"] is None
    assert (await request(client, "patch", "/api/v1/sessions/no-ttl/config", json={}))[
        "expires_at"
    ] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("ttl_relative", 7),
        ("ttl_relative", None),
        ("ttl_absolute", None),
        ("expires_at", "2999-01-01T00:00:00Z"),
        ("ttl_days", 7),
        ("ttl_per_file", True),
    ],
)
async def test_object_ttl_inputs_are_rejected_including_null(client, field, value):
    response = await client.post("/api/v1/sessions", json={field: value})
    assert response.status_code == 400, response.text
    response = await client.patch("/api/v1/sessions/s1/config", json={field: value})
    assert response.status_code == 400, response.text
    response = await client.patch(
        "/api/v1/content/ttl", json={"uri": ROOT + "/sessions/s1", field: value}
    )
    assert response.status_code == 405


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/2026", "/2026/09", "/2026/09/28", "/2026/09/28/a.md"])
async def test_only_policy_roots_are_editable(client, suffix):
    response = await client.patch(
        CONFIG,
        json={
            "settings": {
                "ttl": {
                    "directories": {
                        ROOT + "/memories/events" + suffix: {"mode": "days", "ttl_days": 7}
                    }
                }
            }
        },
    )
    assert response.status_code == 400, response.text


@pytest.mark.asyncio
async def test_event_visibility_projection_and_all_public_filters(client, service):
    ctx = root_ctx()
    parent = ROOT + "/memories/events"
    owner = parent + "/2026/09/28"
    sibling = parent + "/2026/09/29"
    fs = service.viking_fs
    await request(
        client,
        "patch",
        CONFIG,
        json={"settings": {"ttl": {"directories": {parent: {"mode": "days", "ttl_days": 7}}}}},
    )
    await fs.write_file(owner + "/a.txt", "find TTL test", ctx=ctx)
    await fs.write_file(owner + "/nested/b.txt", "find TTL test nested", ctx=ctx)
    await fs.write_file(sibling + "/live.txt", "find TTL test live", ctx=ctx)
    expiry = (await request(client, "get", "/api/v1/content/ttl", params={"uri": owner}))[
        "expires_at"
    ]
    for endpoint in ["stat", "attrs"]:
        result = await request(
            client, "get", "/api/v1/fs/" + endpoint, params={"uri": owner + "/a.txt"}
        )
        assert result["expires_at"] == expiry
    for endpoint in ["ls", "tree"]:
        rows = await request(client, "get", "/api/v1/fs/" + endpoint, params={"uri": owner})
        assert rows and all(row["expires_at"] == expiry for row in rows)
    response = await client.get("/api/v1/content/read", params={"uri": owner + "/a.txt"})
    assert response.json()["expires_at"] == expiry and isinstance(response.json()["result"], str)
    for endpoint, body in [
        ("grep", {"uri": owner, "pattern": "find"}),
        ("glob", {"uri": owner, "pattern": "**/*.txt", "extra_fields": []}),
    ]:
        result = await request(client, "post", "/api/v1/search/" + endpoint, json=body)
        assert result["matches"] and all(row["expires_at"] == expiry for row in result["matches"])
    await fs.write_file(
        owner + "/.meta.json", json.dumps({"expires_at": "2000-01-01T00:00:00Z"}), ctx=ctx
    )
    for endpoint in [
        "/api/v1/fs/stat",
        "/api/v1/fs/attrs",
        "/api/v1/content/read",
        "/api/v1/content/download",
    ]:
        response = await client.get(endpoint, params={"uri": owner + "/a.txt"})
        assert response.status_code == 404, response.text
    for endpoint in ["ls", "tree"]:
        rows = await request(
            client, "get", "/api/v1/fs/" + endpoint, params={"uri": parent, "level_limit": 5}
        )
        assert all(not row["uri"].startswith(owner) for row in rows)
    for endpoint, body in [
        ("grep", {"uri": parent, "pattern": "find"}),
        ("glob", {"uri": parent, "pattern": "**/*.txt"}),
    ]:
        result = await request(client, "post", "/api/v1/search/" + endpoint, json=body)
        assert result["matches"]
        assert all(
            not (row["uri"] if isinstance(row, dict) else row).startswith(owner)
            for row in result["matches"]
        )


@pytest.mark.asyncio
async def test_expired_session_is_hidden_and_rejects_id_reuse(client, service):
    await request(client, "post", "/api/v1/sessions", json={"session_id": "expired"})
    uri = ROOT + "/sessions/expired"
    await service.viking_fs.write_file(
        uri + "/.meta.json",
        json.dumps({"session_id": "expired", "expires_at": "2000-01-01T00:00:00Z"}),
        ctx=root_ctx(),
    )
    for suffix in [
        "",
        "/context",
        "/tool-results",
        "/tool-results/a",
        "/tool-results/a/search?q=a",
        "/archives/archive_001",
    ]:
        response = await client.get("/api/v1/sessions/expired" + suffix)
        assert response.status_code == 404, response.text
    listed = await request(client, "get", "/api/v1/sessions")
    assert all(row["session_id"] != "expired" for row in listed)
    recreated = await client.post("/api/v1/sessions", json={"session_id": "expired"})
    assert recreated.status_code == 409, recreated.text


@pytest.mark.asyncio
async def test_find_filters_expired_vectors_and_returns_each_owner_expiry(client, service):
    from openviking.storage.vector_ids import vector_record_id

    fs, ctx = service.viking_fs, root_ctx()
    parent = ROOT + "/memories/events"
    expiries = ["2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z", None]
    expected = {}
    for day, expiry in enumerate(expiries, start=1):
        owner = parent + f"/2026/09/{day:02}"
        uri = owner + "/match.txt"
        await fs.write_file(uri, "TTL retrieval acceptance", ctx=ctx)
        if expiry:
            await fs.write_file(owner + "/.meta.json", json.dumps({"expires_at": expiry}), ctx=ctx)
        await fs.vector_store.upsert(
            {
                "id": vector_record_id(ctx.account_id, uri, 2),
                "uri": uri,
                "parent_uri": owner,
                "level": 2,
                "is_leaf": True,
                "context_type": "memory",
                "category": "events",
                "abstract": "TTL retrieval acceptance",
                "vector": [0.1] * get_openviking_config().embedding.dimension,
            },
            ctx=ctx,
        )
        if day > 1:
            expected[uri] = expiry
    result = await request(
        client,
        "post",
        "/api/v1/search/find",
        json={
            "query": "TTL retrieval acceptance",
            "target_uri": parent,
            "level": 2,
            "limit": 2,
        },
    )
    assert {row["uri"]: row["expires_at"] for row in result["memories"]} == expected


@pytest.mark.asyncio
async def test_root_patch_updates_existing_event_and_session_before_return(client, service):
    ctx = root_ctx()
    event = ROOT + "/memories/events/2026/10/01"
    await service.viking_fs.write_file(event + "/body.txt", "historical body", ctx=ctx)
    created = await request(client, "post", "/api/v1/sessions", json={"session_id": "existing"})
    assert created["expires_at"] is None
    await request(
        client,
        "patch",
        CONFIG,
        json={"settings": {"ttl": {"global": {"mode": "days", "ttl_days": 7}}}},
    )
    event_fields = await request(client, "get", "/api/v1/content/ttl", params={"uri": event})
    session_fields = await request(client, "get", "/api/v1/sessions/existing")
    assert event_fields["expires_at"] and session_fields["expires_at"]
    await request(
        client,
        "patch",
        CONFIG,
        json={"settings": {"ttl": {"global": {"mode": "days", "ttl_days": 30}}}},
    )
    extended = await request(client, "get", "/api/v1/content/ttl", params={"uri": event})
    from datetime import timedelta

    from openviking.utils.time_utils import parse_iso_datetime

    assert parse_iso_datetime(extended["expires_at"]) - parse_iso_datetime(
        event_fields["expires_at"]
    ) == timedelta(days=23)
    await request(
        client,
        "patch",
        CONFIG,
        json={
            "settings": {
                "ttl": {
                    "directories": {
                        ROOT + "/memories/events": {"mode": "absolute", "ttl_absolute": 1000000000}
                    }
                }
            }
        },
    )
    assert (
        await client.get("/api/v1/content/read", params={"uri": event + "/body.txt"})
    ).status_code == 404
    updated_session = await request(client, "get", "/api/v1/sessions/existing")
    assert parse_iso_datetime(updated_session["expires_at"]) - parse_iso_datetime(
        session_fields["expires_at"]
    ) == timedelta(days=23)
    await request(
        client, "patch", CONFIG, json={"settings": {"ttl": {"global": {"mode": "disabled"}}}}
    )
    assert (await request(client, "get", "/api/v1/sessions/existing"))["expires_at"] is None


@pytest.mark.parametrize(
    "policy", [{"message_count_threshold": None}, {"message_count_threshhold": 10}]
)
async def test_auto_commit_policy_rejects_invalid_fields(client: httpx.AsyncClient, policy):
    resp = await client.post("/api/v1/sessions", json={"auto_commit_policy": policy})
    assert resp.status_code == 400
    if "message_count_threshold" in policy:
        assert "auto_commit_policy=null" in resp.json()["error"]["message"]

    created = await client.post("/api/v1/sessions", json={})
    session_id = created.json()["result"]["session_id"]
    resp = await client.patch(
        f"/api/v1/sessions/{session_id}/config", json={"auto_commit_policy": policy}
    )
    assert resp.status_code == 400
