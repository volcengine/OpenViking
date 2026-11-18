"""OpenViking key partitions isolate state and expire without changing session identity."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from aiohttp import web
from conftest import default_upstreams, make_due

from openviking_gateway import storage
from openviking_gateway.cli import load_config
from openviking_gateway.config import GatewayConfig
from openviking_gateway.storage import digest

KEYS = ("library-a", "library-b")


@pytest.fixture
def gateway_config(request, gateway_backend):
    return {
        "management_api": False,
        "default_upstreams": default_upstreams(gateway_backend[0]),
        "openviking_keys": True,
        "tenancy": "key",
        "session_ttl_seconds": 7 * 86400,
        "default_profile": {"gateway_tools": False},
        **getattr(request, "param", {}),
    }


def bearer(key):
    return {"Authorization": "Bearer " + key}


async def credential(app, key):
    return await app.state.management.credential(digest("openviking\0" + key))


async def chat(client, key, messages):
    response = await client.post(
        "/v1/chat/completions",
        headers={**bearer(key), "X-OpenViking-Session": "shared-name"},
        json={"model": "model", "messages": messages},
    )
    assert response.status_code == 200, response.text
    return response


async def test_same_identity_keys_isolate_recall_capture_models_and_logs(running_gateway):
    app, client, _, minted, captured, writes = running_gateway
    assert minted is None
    calls = []

    async def handler(request):
        calls.append((request.path, request.headers.get("X-API-Key"), await request.read()))
        if request.path == "/api/v1/search/search":
            key = request.headers["X-API-Key"]
            return web.json_response({"result": {"entries": [], "rendered": "memory-" + key}})
        return None

    app.state.test_backend["handler"] = handler
    records = []
    for key in KEYS:
        assert (await client.get("/v1/models", headers=bearer(key))).status_code == 200
        records.append(await credential(app, key))
    assert records[0]["account"] != records[1]["account"]
    assert {r["openviking_account"] for r in records} == {"tenant"}
    assert {r["user_id"] for r in records} == {"alice"}
    for record, key in zip(records, KEYS, strict=True):
        upstream = app.state.config.default_upstreams["chat"].model_dump()
        await app.state.management.save(
            record["account"], "upstreams", "chat", {**upstream, "models": ["model", key]}
        )
        models = (await client.get("/v1/models", headers=bearer(key))).json()["data"]
        assert {m["id"] for m in models} == {"model", key}
        await chat(client, key, [{"role": "user", "content": "private-" + key}])
        body = captured[-1][1].decode()
        assert "memory-" + key in body
        assert all("memory-" + other not in body for other in KEYS if other != key)
    session, targets = digest("shared-name"), {}
    for record in records:
        scope = digest(record["account"] + "\0alice\0chat")
        document = await app.state.store.capture.get(scope, session)
        targets[document.value["account"]] = document.value["ov_session"]
    assert len(set(targets.values())) == 2
    await make_due(app.state.store)
    for _ in range(6):
        if len(writes) == 2:
            break
        await app.state.worker.once()
    assert len(writes) == 2
    for record, key in zip(records, KEYS, strict=True):
        delivered = [
            json.loads(body)
            for path, used_key, body in calls
            if path == f"/api/v1/sessions/{targets[record['account']]}/messages/batch"
            and used_key == key
        ]
        assert len(delivered) == 1
        assert "private-" + key in json.dumps(delivered)
        assert all(
            "private-" + other not in json.dumps(delivered) for other in KEYS if other != key
        )
        logs = await app.state.management.logs(record["account"])
        assert logs and all(log.get("degradation") is None for log in logs)
        assert {log["session"] for log in logs} == {session}
    assert await app.state.management.logs("tenant") == []


@pytest.mark.parametrize("gateway_config", [{"tenancy": "account"}], indirect=True)
async def test_account_tenancy_shares_a_users_session(running_gateway):
    app, client, _, _, _, _ = running_gateway
    for key in KEYS:
        await chat(client, key, [{"role": "user", "content": "same message"}])
        record = await credential(app, key)
        assert record["account"] == record["openviking_account"] == "tenant"
    shared = await app.state.store.capture.get(digest("tenant\0alice\0chat"), digest("shared-name"))
    assert shared.value["pending"]


@pytest.mark.parametrize(
    ("gateway_config", "after"),
    [({"tenancy": "account"}, "key"), ({"tenancy": "key"}, "account")],
    ids=["account-to-key", "key-to-account"],
    indirect=["gateway_config"],
)
async def test_tenancy_change_moves_a_stored_key_to_its_partition(running_gateway, after):
    app, client, _, _, _, _ = running_gateway
    identifier = digest("openviking\0" + KEYS[0])
    partitions = {"account": "tenant", "key": "k_" + identifier}
    before = partitions[app.state.config.tenancy]
    assert (await client.get("/v1/models", headers=bearer(KEYS[0]))).status_code == 200
    assert (await credential(app, KEYS[0]))["account"] == before
    app.state.config.tenancy = after
    assert (await client.get("/v1/models", headers=bearer(KEYS[0]))).status_code == 200
    assert (await credential(app, KEYS[0]))["account"] == partitions[after]
    assert await app.state.management.get(before, "keys", identifier) is None


@pytest.mark.parametrize("field", ["account_id", "user_id"])
async def test_changed_identity_revokes_key_partition(running_gateway, field):
    app, client, _, _, _, _ = running_gateway
    assert (await client.get("/v1/models", headers=bearer(KEYS[0]))).status_code == 200

    async def handler(request):
        if request.path == "/health":
            identity = {
                "version": "0.4.16",
                "auth_mode": "api_key",
                "role": "user",
                "account_id": "tenant",
                "user_id": "alice",
                field: "changed",
            }
            return web.json_response(identity)
        return None

    app.state.test_backend["handler"] = handler
    app.state.viking.identities.clear()
    assert (await client.get("/v1/models", headers=bearer(KEYS[0]))).status_code == 401
    assert await credential(app, KEYS[0]) is None


async def test_dev_identity_does_not_revoke_key_partition(running_gateway):
    app, client, _, _, _, _ = running_gateway
    assert (await client.get("/v1/models", headers=bearer(KEYS[0]))).status_code == 200
    before = await credential(app, KEYS[0])
    app.state.test_backend["auth_mode"] = "dev"
    app.state.viking.identities.clear()
    assert (await client.get("/v1/models", headers=bearer(KEYS[0]))).status_code == 200
    assert await credential(app, KEYS[0]) == before


@pytest.mark.parametrize("source", ["openviking", "issued"])
@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("gateway_config", [{"tenancy": "account"}], indirect=True)
async def test_legacy_and_issued_records_compare_the_stored_account(
    running_gateway, source, changed
):
    app, client, _, _, _, _ = running_gateway
    key = KEYS[0] if source == "openviking" else "ovgw_legacy"
    identifier = digest("openviking\0" + key) if source == "openviking" else digest(key)
    await app.state.management.save(
        "other" if changed else "tenant",
        "keys",
        identifier,
        {
            "source": source,
            "openviking_key": KEYS[0],
            "user_id": "alice",
            "policy_id": "default",
            "upstream_ids": [],
            "models": [],
        },
    )
    response = await client.get("/v1/models", headers=bearer(key))
    assert response.status_code == (401 if changed else 200), response.text
    assert bool(await app.state.management.credential(identifier)) is not changed


async def test_fixed_key_and_user_ttl_rebuilds_the_same_scope(running_gateway, monkeypatch):
    app, client, _, _, _, _ = running_gateway
    management = app.state.management
    now = time.time()
    monkeypatch.setattr(storage, "time", SimpleNamespace(time=lambda: now))
    history = [{"role": "user", "content": "keep this session"}]
    await chat(client, KEYS[0], history)
    before = await credential(app, KEYS[0])
    user = await management.get(before["account"], "users", "alice")
    assert before["expires_at"] == user["expires_at"] == now + 7 * 86400
    scope, session = digest(before["account"] + "\0alice\0chat"), digest("shared-name")
    initial = await app.state.store.capture.get(scope, session)
    now += 86400
    assert (await client.get("/v1/models", headers=bearer(KEYS[0]))).status_code == 200
    assert (await credential(app, KEYS[0]))["expires_at"] == before["expires_at"]
    assert (await management.get(before["account"], "users", "alice")) == user
    now += 6 * 86400
    management.cache.clear()
    assert await credential(app, KEYS[0]) is None
    assert await management.get(before["account"], "users", "alice") is None
    history += [{"role": "assistant", "content": "hello"}, {"role": "user", "content": "next"}]
    await chat(client, KEYS[0], history)
    after = await credential(app, KEYS[0])
    assert after["account"] == before["account"]
    assert after["expires_at"] == now + 7 * 86400
    assert (await management.get(after["account"], "users", "alice"))["expires_at"] == after[
        "expires_at"
    ]
    assert (await app.state.store.capture.get(scope, session)).value["ov_session"] == initial.value[
        "ov_session"
    ]


async def test_concurrent_first_requests_create_one_partition(running_gateway):
    app, client, _, _, _, _ = running_gateway
    responses = await asyncio.gather(
        *(client.get("/v1/models", headers=bearer(KEYS[0])) for _ in range(12))
    )
    assert {r.status_code for r in responses} == {200}
    record = await credential(app, KEYS[0])
    assert len(await app.state.management.list(record["account"], "keys")) == 1
    assert len(await app.state.management.list(record["account"], "users")) == 1


def test_tenancy_constraint_is_checked_when_loading_config(tmp_path, monkeypatch):
    path = tmp_path / "gateway.conf"
    path.write_text(json.dumps({"openviking_url": "http://localhost:1933", "tenancy": "key"}))
    monkeypatch.setenv("OPENVIKING_GATEWAY_CONFIG_FILE", str(path))
    with pytest.raises(ValueError, match="tenancy 'key' requires management_api: false"):
        load_config()
    monkeypatch.setenv("OPENVIKING_GATEWAY_MANAGEMENT_API", "false")
    assert load_config().tenancy == "key"
    assert load_config().openviking_keys is False
    assert GatewayConfig().tenancy == "account"
    with pytest.raises(ValueError):
        GatewayConfig(tenancy="resource", management_api=False)
