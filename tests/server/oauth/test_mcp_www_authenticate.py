# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the WWW-Authenticate hint emitted by the MCP middleware on 401.

Ensures Claude.ai / Claude Desktop can auto-discover the OAuth authorization
server per RFC 9728 §5.1.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.config import ServerConfig
from openviking.server.identity import ResolvedIdentity, Role
from openviking.server.mcp_endpoint import _get_ctx, _IdentityASGIMiddleware
from openviking.server.oauth.provider import OpenVikingOAuthProvider
from openviking.server.oauth.storage import OAuthStore
from openviking_cli.exceptions import UnauthenticatedError


async def _noop_app(scope, receive, send):
    """Minimal downstream ASGI app that asserts the middleware never reaches it."""
    raise AssertionError("Downstream app should not be called for unauthenticated requests")


async def _ok_app(scope, receive, send):
    ctx = _get_ctx()
    response = httpx.Response(
        200,
        json={
            "role": str(ctx.role),
            "account_id": ctx.user.account_id,
            "user_id": ctx.user.user_id,
        },
    )
    await send(
        {
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": response.content})


def _build_test_app(*, oauth_enabled: bool, tmp_path=None) -> FastAPI:
    from openviking.server.auth.plugins import ApiKeyAuthPlugin
    from openviking.server.auth.registry import get_registry

    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="api_key", root_api_key="root-test-1234567890abcd")
    app.state.api_key_manager = object()  # presence triggers API_KEY auth path
    # Set auth plugin (lifespan not triggered in ASGI tests)
    registry = get_registry()
    if registry.get("api_key") is None:
        registry.register(ApiKeyAuthPlugin)
    app.state.auth_plugin = registry.get("api_key")()
    if oauth_enabled:
        # Provider just needs to exist on app.state to flag oauth as enabled —
        # the bearer middleware emits WWW-Authenticate based on its presence,
        # not on any specific verifier behavior.
        app.state.oauth_provider = OpenVikingOAuthProvider(
            store=OAuthStore(tmp_path / "oauth.db") if tmp_path else None,
            issuer="http://ov.test",
        )
    return app


class _RootOnlyKeyManager:
    def resolve(self, api_key):
        if api_key != "root-test-1234567890abcd":
            raise UnauthenticatedError("Invalid API Key")
        return ResolvedIdentity(
            role=Role.ROOT,
            account_id="default",
            user_id="default",
        )


class _UserOnlyKeyManager:
    def resolve(self, api_key):
        if api_key != "user-test-1234567890abcd":
            raise UnauthenticatedError("Invalid API Key")
        return ResolvedIdentity(
            role=Role.USER,
            account_id="default",
            user_id="alice",
        )


def _mount_mcp(app: FastAPI, downstream=_noop_app) -> None:
    """Mount a tiny ASGI route at /mcp wrapped in _IdentityASGIMiddleware."""
    from starlette.routing import Route

    handler = _IdentityASGIMiddleware(downstream)
    app.routes.append(Route("/mcp", endpoint=handler, methods=["GET", "POST"]))


@pytest.mark.asyncio
async def test_unauthenticated_request_includes_www_authenticate_when_oauth_enabled():
    app = _build_test_app(oauth_enabled=True)
    _mount_mcp(app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ov.test") as client:
        resp = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401
    auth_header = resp.headers.get("www-authenticate", "")
    assert auth_header.startswith("Bearer "), auth_header
    assert "resource_metadata=" in auth_header
    assert "/.well-known/oauth-protected-resource" in auth_header
    # The origin is derived from the request Host header.
    assert "ov.test" in auth_header


@pytest.mark.asyncio
async def test_unauthenticated_request_omits_header_when_oauth_disabled():
    """If OAuth is not configured, the 401 body still appears but the hint is absent."""
    app = _build_test_app(oauth_enabled=False)
    _mount_mcp(app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ov.test") as client:
        resp = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401
    assert "www-authenticate" not in {k.lower() for k in resp.headers.keys()}


@pytest.mark.asyncio
async def test_root_api_key_is_rejected_on_mcp_data_plane_in_api_key_mode():
    app = _build_test_app(oauth_enabled=False)
    app.state.api_key_manager = _RootOnlyKeyManager()
    _mount_mcp(app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ov.test") as client:
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer root-test-1234567890abcd"},
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == -32001
    assert "ROOT API keys cannot access tenant-scoped data APIs" in resp.text


@pytest.mark.asyncio
async def test_user_api_key_is_allowed_on_mcp_data_plane_in_api_key_mode():
    app = _build_test_app(oauth_enabled=False)
    app.state.api_key_manager = _UserOnlyKeyManager()
    _mount_mcp(app, downstream=_ok_app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ov.test") as client:
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer user-test-1234567890abcd"},
        )

    assert resp.status_code == 200
    assert resp.json() == {
        "role": "user",
        "account_id": "default",
        "user_id": "alice",
    }


@pytest.mark.asyncio
async def test_www_authenticate_honors_x_forwarded_proto():
    app = _build_test_app(oauth_enabled=True)
    _mount_mcp(app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ov.test") as client:
        resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "public.example.com",
            },
        )
    assert resp.status_code == 401
    auth_header = resp.headers.get("www-authenticate", "")
    assert "https://public.example.com/.well-known/oauth-protected-resource" in auth_header
