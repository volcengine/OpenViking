# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""TTL configuration and live-document changes through the public HTTP surface."""

import asyncio
import json
import os
import socket
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn

from openviking.core import ttl
from openviking.service.ttl_cleanup import TTLCleanupService
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from openviking_cli.utils.config import get_openviking_config, set_openviking_config
from tests.storage.test_transfer_merge_binding import root_ctx
from tests.unit.service.test_ttl_cleanup import _cleanup_once
from tests.unit.storage.test_resource_ttl import fs_ctx as fs_ctx

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


async def write(client, uri):
    await request(
        client,
        "post",
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "Keep this document.",
            "mode": "create",
            "processing_mode": "vectors_only",
            "wait": True,
        },
    )
    return await request(client, "get", "/api/v1/content/ttl", params={"uri": uri})


async def rewrite(client, uri, *, mode):
    return await request(
        client,
        "post",
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": " Updated.",
            "mode": mode,
            "processing_mode": "vectors_only",
            "wait": True,
        },
    )


@pytest.mark.asyncio
async def test_account_configuration_reaches_all_creation_paths_and_is_incremental(client, service):
    await request(
        client,
        "patch",
        "/api/v1/admin/configuration",
        json={
            "settings": {"ttl": {"global": {"mode": "days", "ttl_days": 30}}},
        },
    )
    await request(
        client,
        "patch",
        CONFIG,
        json={
            "settings": {
                "ttl": {
                    "global": {"mode": "days", "ttl_days": 20},
                    "resources": {"mode": "days", "ttl_days": 20},
                    "directories": {
                        ROOT + "/memories/events": {"mode": "days", "ttl_days": 7},
                        ROOT + "/memories/events/2026": {"mode": "days", "ttl_days": 5},
                    },
                }
            }
        },
    )
    snapshots = {}
    for suffix, days in [
        ("memories/events/a.MD", 7),
        ("memories/events/2026/a.txt", 5),
        ("peers/p1/memories/events/.note.txt", 20),
        ("resources/a.txt", 20),
    ]:
        uri = ROOT + "/" + suffix
        fields = await write(client, uri)
        assert fields["ttl_days"] == days
        assert parse_iso_datetime(fields["expires_at"]) - parse_iso_datetime(
            fields["received_at"]
        ) == timedelta(days=days)
        snapshots[uri] = fields
    await request(client, "post", "/api/v1/sessions", json={"session_id": "ttl-config"})
    meta = json.loads(
        await service.viking_fs.read_file(ROOT + "/sessions/ttl-config/.meta.json", ctx=root_ctx())
    )
    assert meta["ttl_days"] == 20
    assert (await write(client, "viking://resources/library.txt"))["ttl_days"] == 20
    # Removing this account's override restores the existing cluster baseline.
    await request(client, "patch", CONFIG, json={"settings": {"ttl": None}})
    assert (await write(client, ROOT + "/memories/events/new.txt"))["ttl_days"] == 30
    for uri, frozen in snapshots.items():
        assert await request(client, "get", "/api/v1/content/ttl", params={"uri": uri}) == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix",
    [
        "memories/events/a.txt",
        "memories/events/a.MD",
        "memories/events/.note.txt",
        "peers/p1/memories/events/a.txt",
        "resources/a.txt",
    ],
)
async def test_expiry_change_supersedes_cleanup_and_preserves_content(
    client, service, monkeypatch, suffix
):
    await request(
        client,
        "patch",
        CONFIG,
        json={
            "settings": {
                "ttl": {
                    "global": {"mode": "days", "ttl_days": 7},
                    "resources": {"mode": "days", "ttl_days": 7},
                }
            },
        },
    )
    uri = ROOT + "/" + suffix
    original = await write(client, uri)
    fs, ctx = service.viking_fs, root_ctx()
    raw = await fs.read_file(uri, ctx=ctx)
    record = await fs.ttl_registry.get(ctx.account_id, uri)
    old_expiry = parse_iso_datetime(original["expires_at"])
    new_expiry = format_iso8601(old_expiry + timedelta(days=7))
    changed = await request(
        client,
        "patch",
        "/api/v1/content/ttl",
        json={
            "uri": uri,
            "expires_at": new_expiry,
        },
    )
    assert changed == {**original, "expires_at": new_expiry, "ttl_days": None}
    assert (await fs.ttl_registry.get(ctx.account_id, uri)).expires_at == new_expiry
    if "/events/" in uri:
        before, after = (
            MemoryFileUtils.read(raw),
            MemoryFileUtils.read(await fs.read_file(uri, ctx=ctx)),
        )
        assert before.content == after.content
        assert after.extra_fields == {
            **before.extra_fields,
            "expires_at": new_expiry,
            "ttl_days": None,
        }
    else:
        assert await fs.read_file(uri, ctx=ctx) == raw
    real_expired = ttl.is_expired
    monkeypatch.setattr(
        ttl,
        "is_expired",
        lambda value, **_: real_expired(value, now=old_expiry + timedelta(seconds=1)),
    )
    cleanup = TTLCleanupService(service=service, service_loop=asyncio.get_running_loop())
    assert (await _cleanup_once(cleanup, record))["skipped"] == "renewed"
    assert await fs.exists(uri, ctx=ctx)
    monkeypatch.setattr(
        ttl,
        "is_expired",
        lambda value, **_: real_expired(value, now=old_expiry + timedelta(days=8)),
    )
    response = await client.patch(
        "/api/v1/content/ttl",
        json={
            "uri": uri,
            "expires_at": format_iso8601(old_expiry + timedelta(days=20)),
        },
    )
    assert response.status_code == 404
    assert (await _cleanup_once(cleanup, await fs.ttl_registry.get(ctx.account_id, uri)))["deleted"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["replace", "append"])
@pytest.mark.parametrize(
    "suffix,policy",
    [
        ("memories/events/updated.txt", "user_events"),
        ("resources/updated.txt", "resources"),
    ],
)
async def test_successful_content_update_renews_relative_deadline(
    client, service, mode, suffix, policy
):
    await request(
        client,
        "patch",
        CONFIG,
        json={"settings": {"ttl": {policy: {"mode": "days", "ttl_days": 7}}}},
    )
    uri = ROOT + "/" + suffix
    original = await write(client, uri)
    await asyncio.sleep(0.01)

    await rewrite(client, uri, mode=mode)

    renewed = await request(client, "get", "/api/v1/content/ttl", params={"uri": uri})
    assert renewed["ttl_generation"] == original["ttl_generation"]
    assert renewed["ttl_days"] == 7
    assert parse_iso_datetime(renewed["received_at"]) > parse_iso_datetime(original["received_at"])
    assert parse_iso_datetime(renewed["expires_at"]) - parse_iso_datetime(
        renewed["received_at"]
    ) == timedelta(days=7)
    record = await service.viking_fs.ttl_registry.get("default", uri)
    assert record.expires_at == renewed["expires_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["replace", "append"])
async def test_manual_absolute_deadline_does_not_move_on_resource_update(client, mode):
    await request(
        client,
        "patch",
        CONFIG,
        json={"settings": {"ttl": {"resources": {"mode": "days", "ttl_days": 7}}}},
    )
    uri = ROOT + "/resources/absolute.txt"
    original = await write(client, uri)
    fixed = format_iso8601(parse_iso_datetime(original["expires_at"]) + timedelta(days=10))
    await request(
        client,
        "patch",
        "/api/v1/content/ttl",
        json={"uri": uri, "expires_at": fixed},
    )

    await rewrite(client, uri, mode=mode)

    unchanged = await request(client, "get", "/api/v1/content/ttl", params={"uri": uri})
    assert unchanged["expires_at"] == fixed
    assert parse_iso_datetime(unchanged["received_at"]) >= parse_iso_datetime(
        original["received_at"]
    )
    assert unchanged["ttl_generation"] == original["ttl_generation"]


@pytest.mark.asyncio
async def test_resource_directory_deadline_edit_is_rejected(client, service):
    directory = ROOT + "/resources/folder"
    await service.viking_fs.mkdir(directory, exist_ok=True, ctx=root_ctx())

    response = await client.patch(
        "/api/v1/content/ttl",
        json={"uri": directory, "expires_at": "2999-01-01T00:00:00.000Z"},
    )

    assert response.status_code == 400, response.text
    assert "resource directories define defaults via resources/config" in response.text


@pytest.mark.asyncio
async def test_cli_sdk_configuration_and_document_expiry_chain(ttl_admin_app, service, tmp_path):
    from openviking_cli.client.http import AsyncHTTPClient

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            ttl_admin_app, host="127.0.0.1", port=port, lifespan="off", log_level="error"
        )
    )
    serving = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started
    url = f"http://127.0.0.1:{port}"
    account = "default"
    api_key = "test-cli-key"
    uri = ROOT + "/memories/events/cli.txt"
    sdk = AsyncHTTPClient(
        url=url,
        api_key=api_key,
        account=account,
        user="default",
        actor_peer_id="",
        extra_headers={},
    )
    await sdk.initialize()
    try:
        policy = {"ttl": {"global": {"mode": "days", "ttl_days": 7}}}
        binary = os.getenv("OV_TTL_TEST_CLI")
        if binary:
            config = tmp_path / "ovcli.conf"
            config.write_text(
                json.dumps(
                    {
                        "url": url,
                        "api_key": api_key,
                        "account": account,
                        "user": "default",
                        "language": "en",
                    }
                )
            )

            async def cli(*args):
                process = await asyncio.create_subprocess_exec(
                    binary,
                    "--output",
                    "json",
                    *args,
                    env={**os.environ, "OPENVIKING_CLI_CONFIG_FILE": str(config)},
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
                assert process.returncode == 0, stderr.decode()
                return json.loads(stdout)["result"]

            await cli(
                "admin",
                "patch-configuration",
                "--account-id",
                account,
                "--settings",
                json.dumps(policy),
            )
        else:
            await sdk.admin_patch_configuration(policy, account)
        assert (await sdk.admin_get_configuration(account))["settings"]["ttl"] == policy["ttl"]
        await sdk.write(
            uri,
            "CLI to HTTP to storage",
            mode="create",
            options={"processing_mode": "vectors_only"},
            wait=True,
        )
        original = await sdk.get_ttl(uri)
        assert original["ttl_days"] == 7
        expiry = format_iso8601(parse_iso_datetime(original["expires_at"]) + timedelta(days=2))
        if binary:
            await cli("ttl", "set", uri, "--expires-at", expiry)
            assert (await cli("ttl", "get", uri))["expires_at"] == expiry
        else:
            await sdk.update_ttl(uri, expiry)
        assert (await sdk.get_ttl(uri))["expires_at"] == expiry
        record = await service.viking_fs.ttl_registry.get(account, uri)
        assert record.expires_at == expiry
    finally:
        await sdk.close()
        server.should_exit = True
        await asyncio.wait_for(serving, 5)


@pytest.mark.asyncio
async def test_mcp_expiry_edit_uses_request_identity_and_shared_registry(client, service):
    import ast

    from openviking.server import mcp_endpoint

    await request(
        client,
        "patch",
        CONFIG,
        json={"settings": {"ttl": {"user_events": {"mode": "days", "ttl_days": 7}}}},
    )
    uri = ROOT + "/memories/events/mcp.txt"
    original = await write(client, uri)
    expiry = format_iso8601(parse_iso_datetime(original["expires_at"]) + timedelta(days=1))
    token = mcp_endpoint._mcp_ctx.set(root_ctx())
    try:
        alias = "viking://~/memories/events/mcp.txt"
        before = ast.literal_eval(await mcp_endpoint.get_ttl(alias))
        assert before["uri"] == uri
        updated = ast.literal_eval(await mcp_endpoint.update_ttl(alias, expiry))
        assert updated["expires_at"] == expiry
        assert updated["ttl_generation"] == original["ttl_generation"]
        record = await service.viking_fs.ttl_registry.get("default", uri)
        assert record.expires_at == expiry
    finally:
        mcp_endpoint._mcp_ctx.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/u1/memories/events/manual.txt",
        "viking://user/u1/resources/manual.txt",
        "viking://resources/manual.txt",
    ],
)
async def test_sdk_and_mcp_set_unmanaged_file_retention_without_global_policy(
    fs_ctx, monkeypatch, uri
):
    """Public SDK -> HTTP -> service -> real VFS, without a native engine dependency."""
    import ast

    from openviking.server import mcp_endpoint
    from openviking.server.app import create_app
    from openviking.server.auth import get_request_context
    from openviking.server.routers import content, resources
    from openviking.service.fs_service import FSService
    from openviking.service.resource_service import ResourceService
    from openviking_cli.client.http import AsyncHTTPClient

    fs, ctx = fs_ctx
    service = SimpleNamespace(fs=FSService(viking_fs=fs), resources=ResourceService(viking_fs=fs))
    for module in (content, resources, mcp_endpoint):
        monkeypatch.setattr(module, "get_service", lambda: service)
    app = create_app()
    app.dependency_overrides[get_request_context] = lambda: ctx
    await fs.write_file(uri, "retain this body", ctx=ctx)
    raw_stat = fs._async_agfs.stat

    async def timestamped_stat(path, **kwargs):
        return {**await raw_stat(path, **kwargs), "modTime": "2997-01-01T00:00:00Z"}

    monkeypatch.setattr(fs._async_agfs, "stat", timestamped_stat)
    sdk = AsyncHTTPClient(url="http://testserver", account=ctx.account_id, user=ctx.user.user_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        sdk._http = http
        first = await sdk.update_ttl(uri, ttl_relative=7)
        assert first["expires_at"] == "2997-01-08T00:00:00.000Z"
        assert first["ttl_days"] == 7
        assert await sdk.get_ttl(uri) == first
        # Absolute selection must remain fixed even if equal to the relative deadline.
        absolute = await sdk.update_ttl(uri, first["expires_at"])
        assert absolute["ttl_days"] is None
        assert absolute["ttl_generation"] == first["ttl_generation"]
        token = mcp_endpoint._mcp_ctx.set(ctx)
        try:
            changed = ast.literal_eval(await mcp_endpoint.update_ttl(uri, ttl_relative=30))
        finally:
            mcp_endpoint._mcp_ctx.reset(token)
        assert changed["expires_at"] == "2997-01-31T00:00:00.000Z"
        assert changed["received_at"] == first["received_at"]
        if "/resources/" in uri:
            changed = await sdk.update_resource_ttl(uri, ttl_relative=14)
            assert changed["expires_at"] == "2997-01-15T00:00:00.000Z"
        assert (await fs.ttl_registry.get(ctx.account_id, uri)).expires_at == changed["expires_at"]
        assert MemoryFileUtils.read(await fs.read_file(uri, ctx=ctx)).content == "retain this body"
        for payload in (
            {},
            {"ttl_relative": 0},
            {"ttl_relative": True},
            {"ttl_relative": 1, "expires_at": first["expires_at"]},
        ):
            response = await http.patch("/api/v1/content/ttl", json={"uri": uri, **payload})
            assert response.status_code == 400, response.text
        assert await sdk.get_ttl(uri) == changed
