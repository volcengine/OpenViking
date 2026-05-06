# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end tests for the OAuth flow: DCR → OTP → authorize page → token → /mcp.

The flow is:

1. Caller registers a client (SDK's RegistrationHandler).
2. Caller, holding a valid API key, requests an OTP from /api/v1/auth/otp.
3. Caller hits /authorize, which redirects to /oauth/authorize/page?pending=...
4. Caller submits the OTP on the authorize page; we 302 back to redirect_uri
   with a fresh ?code=...
5. Caller exchanges the code at /token (PKCE S256) for an opaque access+refresh.
6. Access token is opaque ovat_-prefixed and resolves through auth.py.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl

from openviking.server.auth import get_request_context
from openviking.server.config import ServerConfig
from openviking.server.identity import ResolvedIdentity, Role
from openviking.server.oauth.provider import OpenVikingOAuthProvider
from openviking.server.oauth.router import router as oauth_router
from openviking.server.oauth.storage import OAuthStore
from openviking_cli.exceptions import OpenVikingError
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS


@dataclass
class _StubOAuthCfg:
    issuer: Optional[str] = "http://127.0.0.1"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 86400
    auth_code_ttl_seconds: int = 300
    otp_ttl_seconds: int = 300


@pytest_asyncio.fixture
async def app_with_oauth(tmp_path):
    """FastAPI app wired with the SDK auth routes + OpenViking authorize page."""
    store = OAuthStore(tmp_path / "oauth.db")
    await store.initialize()
    issuer = "http://127.0.0.1"
    provider = OpenVikingOAuthProvider(store=store, issuer=issuer)

    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="api_key", root_api_key="root-test-1234567890abcd")
    app.state.api_key_manager = object()
    app.state.oauth_store = store
    app.state.oauth_provider = provider
    app.state.oauth_config = _StubOAuthCfg()

    @app.exception_handler(OpenVikingError)
    async def _err(request, exc):  # noqa: ANN001
        return JSONResponse(
            {"error": exc.code, "error_description": exc.message},
            status_code=ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500),
        )

    # Override get_request_context so /api/v1/auth/otp can be hit without
    # a real APIKeyManager. Returns a fixed identity.
    from openviking.server.identity import RequestContext
    from openviking_cli.session.user_id import UserIdentifier

    def _fixed_ctx() -> RequestContext:
        return RequestContext(
            user=UserIdentifier("acct1", "alice", "default"),
            role=Role.USER,
        )

    app.dependency_overrides[get_request_context] = _fixed_ctx
    app.include_router(oauth_router)

    sdk_routes = create_auth_routes(
        provider=provider,
        issuer_url=AnyHttpUrl(issuer),
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )
    app.routes.extend(sdk_routes)

    try:
        yield app, store, provider
    finally:
        await store.close()


@pytest_asyncio.fixture
async def client(app_with_oauth):
    app, _, _ = app_with_oauth
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:64]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# DCR + metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_endpoint(client):
    resp = await client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"].rstrip("/") == "http://127.0.0.1"
    assert "S256" in body["code_challenge_methods_supported"]
    assert "authorization_code" in body["grant_types_supported"]
    assert "refresh_token" in body["grant_types_supported"]
    assert body["registration_endpoint"]


@pytest.mark.asyncio
async def test_dcr_registers_client(client):
    resp = await client.post(
        "/register",
        json={"redirect_uris": ["https://claude.ai/cb"], "client_name": "Claude"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["client_id"]
    assert body["redirect_uris"] == ["https://claude.ai/cb"]


# ---------------------------------------------------------------------------
# OTP issuance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_otp_endpoint_returns_code(client):
    resp = await client.post("/api/v1/auth/otp", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["otp"]) == 6
    assert body["ttl_seconds"] == 300
    assert body["expires_at"] > 0


@pytest.mark.asyncio
async def test_otp_endpoint_rejects_bad_ttl(client):
    resp = await client.post("/api/v1/auth/otp", json={"ttl_seconds": 5})
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Authorize page + OTP submit + token exchange (full happy path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_oauth_flow(app_with_oauth, client):
    _, store, provider = app_with_oauth

    # Step 1: register client.
    reg = await client.post(
        "/register",
        json={
            "redirect_uris": ["https://claude.ai/cb"],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]

    # Step 2: get an OTP using API-key identity (overridden in fixture).
    otp_resp = await client.post("/api/v1/auth/otp", json={})
    otp = otp_resp.json()["otp"]

    # Step 3: kick off authorize. The SDK validates inputs and then calls
    # provider.authorize, which returns a URL to /oauth/authorize/page.
    verifier, challenge = _pkce_pair()
    authorize = await client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": "https://claude.ai/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
        follow_redirects=False,
    )
    assert authorize.status_code == 302, authorize.text
    location = authorize.headers["location"]
    assert "/oauth/authorize/page" in location
    assert "pending=" in location

    # Step 4: GET the page (sanity), then POST the OTP.
    page = await client.get(location, follow_redirects=False)
    assert page.status_code == 200
    assert "One-time passcode" in page.text

    pending = location.split("pending=")[1].split("&")[0]
    submit = await client.post(
        "/oauth/authorize/page",
        data={"pending": pending, "otp": otp},
        follow_redirects=False,
    )
    assert submit.status_code == 302, submit.text
    redirect_target = submit.headers["location"]
    assert redirect_target.startswith("https://claude.ai/cb?")
    assert "code=" in redirect_target
    assert "state=xyz" in redirect_target

    # Step 5: exchange the code for tokens.
    auth_code = redirect_target.split("code=")[1].split("&")[0]
    token_resp = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://claude.ai/cb",
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert token_resp.status_code == 200, token_resp.text
    tokens = token_resp.json()
    assert tokens["token_type"] == "Bearer"
    assert tokens["access_token"].startswith("ovat_")
    assert tokens["refresh_token"].startswith("ovrt_")
    assert tokens["expires_in"] == 3600

    # Step 6: access token resolves through the provider.
    record = await provider.load_access_token(tokens["access_token"])
    assert record is not None
    assert record.account_id == "acct1"
    assert record.user_id == "alice"
    assert record.role == "user"


@pytest.mark.asyncio
async def test_authorize_page_rejects_invalid_otp(app_with_oauth, client):
    _, store, _ = app_with_oauth

    # Register a client and start authorize so we have a pending row.
    reg = await client.post(
        "/register",
        json={"redirect_uris": ["https://x.test/cb"], "token_endpoint_auth_method": "none"},
    )
    cid = reg.json()["client_id"]
    _, challenge = _pkce_pair()
    authorize = await client.get(
        "/authorize",
        params={
            "client_id": cid,
            "response_type": "code",
            "redirect_uri": "https://x.test/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    pending = authorize.headers["location"].split("pending=")[1].split("&")[0]

    submit = await client.post(
        "/oauth/authorize/page",
        data={"pending": pending, "otp": "WRONG1"},
        follow_redirects=False,
    )
    # Stays on form with an error message; pending row still alive.
    assert submit.status_code == 200
    assert "invalid or has already been used" in submit.text


@pytest.mark.asyncio
async def test_refresh_token_rotation(app_with_oauth, client):
    _, _, _ = app_with_oauth
    # Quick way to get an initial token pair: re-run the happy path skeleton.
    reg = await client.post(
        "/register",
        json={"redirect_uris": ["https://x.test/cb"], "token_endpoint_auth_method": "none"},
    )
    cid = reg.json()["client_id"]

    otp = (await client.post("/api/v1/auth/otp", json={})).json()["otp"]
    verifier, challenge = _pkce_pair()
    authorize = await client.get(
        "/authorize",
        params={
            "client_id": cid,
            "response_type": "code",
            "redirect_uri": "https://x.test/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    pending = authorize.headers["location"].split("pending=")[1].split("&")[0]
    submit = await client.post(
        "/oauth/authorize/page",
        data={"pending": pending, "otp": otp},
        follow_redirects=False,
    )
    auth_code = submit.headers["location"].split("code=")[1].split("&")[0]
    token_resp = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://x.test/cb",
            "client_id": cid,
            "code_verifier": verifier,
        },
    )
    rt1 = token_resp.json()["refresh_token"]
    at1 = token_resp.json()["access_token"]

    # Rotate.
    rotated = await client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": rt1, "client_id": cid},
    )
    assert rotated.status_code == 200, rotated.text
    rt2 = rotated.json()["refresh_token"]
    at2 = rotated.json()["access_token"]
    assert rt2 != rt1
    assert at2 != at1

    # Replay the old refresh — must be rejected, AND it should revoke the
    # whole chain (RFC 9700 §4.14). Our OpenViking provider invalidates by
    # (account, user) on replay detection.
    replay = await client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": rt1, "client_id": cid},
    )
    assert replay.status_code == 400
