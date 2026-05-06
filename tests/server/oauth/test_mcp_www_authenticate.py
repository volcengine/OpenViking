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
from openviking.server.mcp_endpoint import _IdentityASGIMiddleware
from openviking.server.oauth.jwt import JwtSigner


async def _noop_app(scope, receive, send):
    """Minimal downstream ASGI app that asserts the middleware never reaches it."""
    raise AssertionError("Downstream app should not be called for unauthenticated requests")


def _build_test_app(*, oauth_enabled: bool) -> FastAPI:
    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="api_key", root_api_key="root-test-1234567890abcd")
    app.state.api_key_manager = object()  # presence triggers API_KEY auth path
    if oauth_enabled:
        app.state.oauth_signer = JwtSigner(b"k" * 32)
    return app


def _mount_mcp(app: FastAPI) -> None:
    """Mount a tiny ASGI route at /mcp wrapped in _IdentityASGIMiddleware."""
    from starlette.routing import Route

    handler = _IdentityASGIMiddleware(_noop_app)
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
