# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Integration tests for openviking/server/oauth/router.py — M2 token endpoint."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openviking.server.oauth.jwt import JwtSigner
from openviking.server.oauth.router import router as oauth_router
from openviking.server.oauth.storage import OAuthStore


@dataclass
class _StubOAuthCfg:
    """In-memory oauth config matching the subset router.py reads."""

    issuer: Optional[str] = "https://ov.test"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 86400
    auth_code_ttl_seconds: int = 300


@pytest_asyncio.fixture
async def app_with_token_endpoint(tmp_path):
    """FastAPI app with /oauth/token mounted and oauth state populated."""
    store = OAuthStore(tmp_path / "oauth.db")
    await store.initialize()
    signer = JwtSigner(b"k" * 32)

    app = FastAPI()
    app.state.oauth_store = store
    app.state.oauth_signer = signer
    app.state.oauth_config = _StubOAuthCfg()

    # Map UnavailableError to 503 like the real app does. Phase 1 tests don't
    # exercise this path (oauth state is always populated), but we keep the
    # handler in place so any future regression surfaces clearly.
    from openviking_cli.exceptions import OpenVikingError
    from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS

    @app.exception_handler(OpenVikingError)
    async def _handler(request, exc):  # noqa: ANN001
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"error": exc.code, "error_description": exc.message},
            status_code=ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500),
        )

    app.include_router(oauth_router)

    try:
        yield app, store, signer
    finally:
        await store.close()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:64]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


@pytest_asyncio.fixture
async def client(app_with_token_endpoint):
    app, _, _ = app_with_token_endpoint
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def issued_code(app_with_token_endpoint):
    """Pre-register a public client and issue an auth code bound to it."""
    _, store, _ = app_with_token_endpoint
    client_record = await store.register_client(
        redirect_uris=["https://example.com/cb"],
        client_name="test-client",
    )
    verifier, challenge = _pkce_pair()
    code_plain = secrets.token_urlsafe(32)
    await store.insert_auth_code(
        code_plain=code_plain,
        client_id=client_record["client_id"],
        redirect_uri="https://example.com/cb",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope="mcp",
        resource="https://ov.test/mcp",
        account_id="acct1",
        user_id="user1",
        role="user",
        ttl_seconds=300,
    )
    return {
        "client_id": client_record["client_id"],
        "code": code_plain,
        "verifier": verifier,
        "challenge": challenge,
    }


@pytest.mark.asyncio
async def test_token_authorization_code_happy_path(app_with_token_endpoint, client, issued_code):
    _, store, signer = app_with_token_endpoint
    resp = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": issued_code["code"],
            "redirect_uri": "https://example.com/cb",
            "client_id": issued_code["client_id"],
            "code_verifier": issued_code["verifier"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "mcp"
    assert body["access_token"]
    assert body["refresh_token"]
    # Cache-Control: no-store per RFC 6749 §5.1.
    assert resp.headers.get("cache-control") == "no-store"
    # JWT decodes back to the bound identity.
    claims = signer.verify(body["access_token"])
    assert claims["account_id"] == "acct1"
    assert claims["user_id"] == "user1"
    assert claims["role"] == "user"
    assert claims["scope"] == "mcp"
    assert claims["aud"] == "https://ov.test/mcp"
    assert claims["client_id"] == issued_code["client_id"]
    # Refresh token persisted.
    assert await store.is_refresh_known_but_consumed(body["refresh_token"]) is False


@pytest.mark.asyncio
async def test_token_unknown_client(client, issued_code):
    resp = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": issued_code["code"],
            "redirect_uri": "https://example.com/cb",
            "client_id": "no-such-client",
            "code_verifier": issued_code["verifier"],
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


@pytest.mark.asyncio
async def test_token_pkce_verifier_mismatch(client, issued_code):
    resp = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": issued_code["code"],
            "redirect_uri": "https://example.com/cb",
            "client_id": issued_code["client_id"],
            "code_verifier": "x" * 64,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_grant"
    assert "PKCE" in body["error_description"]


@pytest.mark.asyncio
async def test_token_redirect_uri_mismatch(client, issued_code):
    resp = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": issued_code["code"],
            "redirect_uri": "https://evil.com/cb",
            "client_id": issued_code["client_id"],
            "code_verifier": issued_code["verifier"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_token_code_reuse(client, issued_code):
    payload = {
        "grant_type": "authorization_code",
        "code": issued_code["code"],
        "redirect_uri": "https://example.com/cb",
        "client_id": issued_code["client_id"],
        "code_verifier": issued_code["verifier"],
    }
    first = await client.post("/oauth/token", data=payload)
    assert first.status_code == 200
    second = await client.post("/oauth/token", data=payload)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_token_missing_required_fields(client):
    resp = await client.post("/oauth/token", data={"grant_type": "authorization_code"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"


@pytest.mark.asyncio
async def test_token_unsupported_grant_type(client):
    resp = await client.post("/oauth/token", data={"grant_type": "client_credentials"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"


@pytest.mark.asyncio
async def test_token_resource_downscope_rejected(client, app_with_token_endpoint, issued_code):
    """If the original code was bound to resource X, asking for Y at exchange must fail."""
    resp = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": issued_code["code"],
            "redirect_uri": "https://example.com/cb",
            "client_id": issued_code["client_id"],
            "code_verifier": issued_code["verifier"],
            "resource": "https://other.example/mcp",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_target"


@pytest.mark.asyncio
async def test_refresh_token_rotates(client, app_with_token_endpoint, issued_code):
    _, store, signer = app_with_token_endpoint
    # First, get a refresh token via authorization_code.
    first = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": issued_code["code"],
            "redirect_uri": "https://example.com/cb",
            "client_id": issued_code["client_id"],
            "code_verifier": issued_code["verifier"],
        },
    )
    rt1 = first.json()["refresh_token"]
    # Now rotate.
    second = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": rt1,
            "client_id": issued_code["client_id"],
        },
    )
    assert second.status_code == 200
    rt2 = second.json()["refresh_token"]
    assert rt2 != rt1
    # rt1 is now consumed; reusing it must fail.
    replay = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": rt1,
            "client_id": issued_code["client_id"],
        },
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    # And rt1 is now flagged consumed in store.
    assert await store.is_refresh_known_but_consumed(rt1) is True


@pytest.mark.asyncio
async def test_refresh_unknown_client(client, app_with_token_endpoint, issued_code):
    resp = await client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": "anything",
            "client_id": "no-such-client",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"
