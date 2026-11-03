# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the signed-token path on GET /api/v1/content/download.

The MCP ``download`` tool mints a single-use ``?token=`` bound to the caller's identity and
one resolved file URI. A GET carrying that token (and no API key) serves only the bound file;
an API-key GET keeps the normal ``?uri=`` behavior used by ``ov get``.
"""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openviking.server import mcp_endpoint
from openviking.server.auth.plugins import ApiKeyAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
from openviking.server.routers import content as content_router
from openviking.server.routers import resources as resources_router
from openviking.server.upload_token_store import download_token_store, upload_token_store
from openviking_cli.exceptions import OpenVikingError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier

TARGET = "viking://resources/specs/design.pdf"
OTHER = "viking://resources/specs/secret.pdf"


@pytest.fixture(autouse=True)
def _reset_token_stores():
    upload_token_store.clear()
    download_token_store.clear()
    yield
    upload_token_store.clear()
    download_token_store.clear()


class _FakeFS:
    def __init__(self):
        self.files = {TARGET: b"%PDF-design", OTHER: b"%PDF-secret"}
        self.denied: set[tuple[str, str]] = set()
        self.reads: list[tuple[str, RequestContext]] = []

    def _check(self, uri, ctx):
        if (uri, ctx.user.user_id) in self.denied:
            raise PermissionDeniedError(f"Access denied for {uri}")

    async def stat(self, uri, ctx, skip_count=False):
        self._check(uri, ctx)
        if uri.endswith("/specs"):
            return {"name": "specs", "size": 0, "isDir": True}
        return {"name": uri.rsplit("/", 1)[-1], "size": len(self.files[uri]), "isDir": False}

    async def read_file_bytes(self, uri, ctx):
        self._check(uri, ctx)
        self.reads.append((uri, ctx))
        return self.files[uri]


@pytest.fixture
async def api_key_manager():
    from openviking.server.store_assembly import build_api_key_manager
    from tests.server.account_store_fakes import InMemoryAGFS

    manager = build_api_key_manager("test-root", SimpleNamespace(agfs=InMemoryAGFS()))
    await manager.load()
    try:
        await manager.create_account("acct", "owner")
        manager.user_key = await manager.register_user("acct", "user", Role.ADMIN)
        yield manager
    finally:
        await manager.close()


@pytest.fixture
def fake_fs(monkeypatch, api_key_manager):
    fs = _FakeFS()
    service = SimpleNamespace(fs=fs)
    config = ServerConfig(auth_mode="api_key", root_api_key="test-root")
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    monkeypatch.setattr(mcp_endpoint, "get_server_config", lambda: config)
    monkeypatch.setattr(content_router, "get_service", lambda: service)
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL", raising=False)
    return fs


@pytest.fixture
async def client(api_key_manager, fake_fs):
    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="api_key", root_api_key="test-root")
    app.state.auth_plugin = ApiKeyAuthPlugin()
    app.state.api_key_manager = api_key_manager
    app.include_router(content_router.router)
    app.include_router(resources_router.router)

    @app.exception_handler(OpenVikingError)
    async def handle_error(_request, exc):
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS[exc.code], content={"error": exc.code}
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _identity(role: Role = Role.ADMIN) -> RequestContext:
    return RequestContext(UserIdentifier("acct", "user"), role, actor_peer_id="bot-a")


async def _mint(uri: str = TARGET, identity: RequestContext | None = None) -> str:
    token = mcp_endpoint._mcp_ctx.set(identity or _identity())
    try:
        result = await mcp_endpoint.download(uri=uri)
    finally:
        mcp_endpoint._mcp_ctx.reset(token)
    url = next(part for part in result.split() if "?token=" in part).strip("'")
    return parse_qs(urlparse(url).query)["token"][0]


async def test_token_serves_bound_file_once_and_ignores_uri_param(client, fake_fs):
    token = await _mint()

    response = await client.get(
        "/api/v1/content/download",
        params={"token": token, "uri": OTHER},
        headers={"X-OpenViking-Account": "other", "X-OpenViking-User": "spoofed"},
    )
    assert response.status_code == 200, response.text
    assert response.content == b"%PDF-design"
    assert response.headers["content-type"] == "application/octet-stream"
    assert "filename*=UTF-8''design.pdf" in response.headers["content-disposition"]
    uri, ctx = fake_fs.reads[-1]
    assert uri == TARGET
    assert ctx.user == UserIdentifier("acct", "user")
    assert ctx.role == Role.ADMIN
    assert ctx.actor_peer_id == "bot-a"

    reused = await client.get("/api/v1/content/download", params={"token": token})
    assert reused.status_code == 401


async def test_expired_token_is_rejected(client, monkeypatch):
    import openviking.server.upload_token_store as store_mod

    now = [1000.0]
    monkeypatch.setattr(store_mod.time, "time", lambda: now[0])
    token = await _mint()
    now[0] += 601

    response = await client.get("/api/v1/content/download", params={"token": token})
    assert response.status_code == 401


async def test_upload_and_download_tokens_are_not_interchangeable(client, upload_temp_dir):
    upload_token, _ = upload_token_store.issue("acct", "user", role=Role.USER, ttl_seconds=600)
    response = await client.get("/api/v1/content/download", params={"token": upload_token})
    assert response.status_code == 401
    assert upload_token_store.peek(upload_token) is not None

    download_token = await _mint()
    response = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": download_token},
        files={"file": ("x.md", b"x", "text/markdown")},
    )
    assert response.status_code == 401
    assert download_token_store.peek(download_token) is not None


async def test_api_key_request_keeps_normal_path(client, api_key_manager, fake_fs):
    token = await _mint()

    response = await client.get(
        "/api/v1/content/download",
        params={"token": token, "uri": OTHER},
        headers={"X-API-Key": api_key_manager.user_key},
    )
    assert response.status_code == 200, response.text
    assert response.content == b"%PDF-secret"
    assert download_token_store.peek(token) is not None

    response = await client.get(
        "/api/v1/content/download", headers={"X-API-Key": api_key_manager.user_key}
    )
    assert response.status_code == 400


async def test_token_loses_authority_after_role_downgrade_or_user_removal(client, api_key_manager):
    token = await _mint()
    await api_key_manager.set_role("acct", "user", Role.USER)
    assert (
        await client.get("/api/v1/content/download", params={"token": token})
    ).status_code == 401

    token = await _mint(identity=_identity(Role.USER))
    await api_key_manager.begin_deletion(
        "acct",
        "user",
        task_id="delete-download-user",
        owner_account_id="acct",
        owner_user_id="owner",
    )
    assert (
        await client.get("/api/v1/content/download", params={"token": token})
    ).status_code == 412

    token = await _mint(identity=_identity(Role.USER))
    assert await api_key_manager.finish_deletion("acct", "user", "delete-download-user")
    assert (
        await client.get("/api/v1/content/download", params={"token": token})
    ).status_code == 401


async def test_access_is_rechecked_at_fetch(client, fake_fs):
    token = await _mint()
    fake_fs.denied.add((TARGET, "user"))

    response = await client.get("/api/v1/content/download", params={"token": token})
    assert response.status_code == 403
    assert fake_fs.reads == []
