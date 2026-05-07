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
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl

from openviking.server.auth import get_request_context
from openviking.server.config import ServerConfig
from openviking.server.identity import Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
from openviking.server.oauth.provider import OpenVikingOAuthProvider
from openviking.server.oauth.router import router as oauth_router
from openviking.server.oauth.storage import OAuthStore
from openviking_cli.exceptions import OpenVikingError


class _FpOnlyKeyManager:
    """Minimal stand-in: returns SHA-256 fingerprints for known (account, user) pairs.

    Router code only touches ``get_user_key_fingerprint`` on this object; the
    full APIKeyManager surface is irrelevant for OAuth flow tests because
    ``get_request_context`` is dependency-overridden to a fixed identity.
    """

    def __init__(self, fingerprints: dict[tuple[str, str], str]):
        self._fps = fingerprints

    def get_user_key_fingerprint(self, account_id: str, user_id: str) -> Optional[str]:
        return self._fps.get((account_id, user_id))


@dataclass
class _StubOAuthCfg:
    # Leave issuer unset so _public_origin falls through to the
    # request's X-Forwarded-*/Host headers — that path is what we want
    # to exercise in PRM tests. Tests that need a fixed issuer can set it
    # directly on the fixture's app.state.oauth_config.
    issuer: Optional[str] = None
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
    # OTP / oauth-verify routes look up the caller's API key fingerprint via
    # ``api_key_manager.get_user_key_fingerprint(account_id, user_id)`` and
    # refuse to issue OAuth state if the call returns None. Provide a tiny
    # stub that returns a stable fingerprint for the fixture's identity.
    app.state.api_key_manager = _FpOnlyKeyManager({("acct1", "alice"): "f" * 64})
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
async def test_protected_resource_metadata(client):
    resp = await client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resource"].endswith("/mcp")
    assert body["authorization_servers"]
    assert "header" in body["bearer_methods_supported"]
    # Must be cacheable.
    assert "max-age" in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_protected_resource_metadata_honors_x_forwarded(client):
    resp = await client.get(
        "/.well-known/oauth-protected-resource",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "public.example"},
    )
    body = resp.json()
    assert body["resource"] == "https://public.example/mcp"


@pytest.mark.asyncio
async def test_protected_resource_metadata_honors_public_base_url_env(client, monkeypatch):
    """OPENVIKING_PUBLIC_BASE_URL must override X-Forwarded-* and Host header."""
    monkeypatch.setenv("OPENVIKING_PUBLIC_BASE_URL", "https://override.example")
    resp = await client.get(
        "/.well-known/oauth-protected-resource",
        headers={"X-Forwarded-Proto": "http", "X-Forwarded-Host": "ignored.example"},
    )
    body = resp.json()
    assert body["resource"] == "https://override.example/mcp"
    assert body["authorization_servers"][0].rstrip("/") == "https://override.example"


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


async def _start_authorize(client, *, redirect_uri="https://claude.ai/cb", state=None):
    """Helper: register a client, kick off /authorize, return (client_id, pending_id, page_url, verifier)."""
    reg = await client.post(
        "/register",
        json={
            "redirect_uris": [redirect_uri],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
        },
    )
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    verifier, challenge = _pkce_pair()
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if state:
        params["state"] = state
    authorize = await client.get("/authorize", params=params, follow_redirects=False)
    assert authorize.status_code == 302, authorize.text
    page_url = authorize.headers["location"]
    pending_id = page_url.split("pending=")[1].split("&")[0]
    return client_id, pending_id, page_url, verifier


@pytest.mark.asyncio
async def test_full_device_flow(app_with_oauth, client):
    """End-to-end: authorize → page shows display_code → console verifies →
    page polls status → 302 → token exchange → access_token resolves."""
    _, store, provider = app_with_oauth

    client_id, pending_id, page_url, verifier = await _start_authorize(
        client, redirect_uri="https://claude.ai/cb", state="xyz"
    )

    # Page renders with the display_code visible.
    page_resp = await client.get(page_url)
    assert page_resp.status_code == 200
    assert "Authorize" in page_resp.text
    pending_record = await store.load_pending_authorization(pending_id)
    assert pending_record is not None
    display_code = pending_record["display_code"]
    assert display_code in page_resp.text

    # Status before verify: pending.
    pre = await client.get("/oauth/authorize/page/status", params={"pending": pending_id})
    assert pre.status_code == 200
    assert pre.json()["status"] == "pending"

    # User confirms in console (auth identity comes from get_request_context override).
    verify = await client.post(
        "/api/v1/auth/oauth-verify",
        json={"code": display_code, "decision": "approve"},
    )
    assert verify.status_code == 200, verify.text
    body = verify.json()
    assert body["status"] == "approved"
    assert body["client_id"] == client_id
    assert body["client_name"] == "Claude"

    # Status after verify: approved + redirect_url with code/state.
    post = await client.get("/oauth/authorize/page/status", params={"pending": pending_id})
    assert post.status_code == 200
    body = post.json()
    assert body["status"] == "approved"
    redirect_url = body["redirect_url"]
    assert redirect_url.startswith("https://claude.ai/cb?")
    assert "code=" in redirect_url
    assert "state=xyz" in redirect_url

    # Polling again after pending row was consumed: gone (410).
    again = await client.get("/oauth/authorize/page/status", params={"pending": pending_id})
    assert again.status_code == 410

    # Token exchange.
    auth_code = redirect_url.split("code=")[1].split("&")[0]
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
    assert tokens["access_token"].startswith("ovat_")
    assert tokens["refresh_token"].startswith("ovrt_")

    # Access token resolves to the verified identity (acct1/alice/user from fixture).
    record = await provider.load_access_token(tokens["access_token"])
    assert record is not None
    assert record.account_id == "acct1"
    assert record.user_id == "alice"
    assert record.role == "user"


@pytest.mark.asyncio
async def test_oauth_verify_unknown_code(app_with_oauth, client):
    _, _, _ = app_with_oauth
    resp = await client.post(
        "/api/v1/auth/oauth-verify",
        json={"code": "BOGUS1", "decision": "approve"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "Invalid" in body.get("error_description", "") or "Invalid" in body.get("message", "")


@pytest.mark.asyncio
async def test_oauth_verify_deny_destroys_pending(app_with_oauth, client):
    _, store, _ = app_with_oauth
    _, pending_id, _, _ = await _start_authorize(client, redirect_uri="https://x.test/cb")
    record = await store.load_pending_authorization(pending_id)
    code = record["display_code"]

    resp = await client.post(
        "/api/v1/auth/oauth-verify",
        json={"code": code, "decision": "deny"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "denied"
    # Pending row gone — page polling now returns 410.
    status = await client.get("/oauth/authorize/page/status", params={"pending": pending_id})
    assert status.status_code == 410


@pytest.mark.asyncio
async def test_status_unknown_pending_returns_410(client):
    resp = await client.get("/oauth/authorize/page/status", params={"pending": "doesnotexist"})
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_oauth_verify_idempotency(app_with_oauth, client):
    """A second verify with the same code must fail — pending is one-shot."""
    _, store, _ = app_with_oauth
    _, pending_id, _, _ = await _start_authorize(client)
    record = await store.load_pending_authorization(pending_id)
    code = record["display_code"]

    first = await client.post(
        "/api/v1/auth/oauth-verify", json={"code": code, "decision": "approve"}
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/auth/oauth-verify", json={"code": code, "decision": "approve"}
    )
    # The pending row's verified flag now blocks find_pending_by_display_code.
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_refresh_token_rotation(app_with_oauth, client):
    _, store, _ = app_with_oauth
    client_id, pending_id, _, verifier = await _start_authorize(
        client, redirect_uri="https://x.test/cb"
    )
    code = (await store.load_pending_authorization(pending_id))["display_code"]

    await client.post("/api/v1/auth/oauth-verify", json={"code": code, "decision": "approve"})
    status = await client.get("/oauth/authorize/page/status", params={"pending": pending_id})
    auth_code = status.json()["redirect_url"].split("code=")[1].split("&")[0]
    token_resp = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://x.test/cb",
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    rt1 = token_resp.json()["refresh_token"]
    at1 = token_resp.json()["access_token"]

    rotated = await client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": rt1, "client_id": client_id},
    )
    assert rotated.status_code == 200
    rt2 = rotated.json()["refresh_token"]
    at2 = rotated.json()["access_token"]
    assert rt2 != rt1 and at2 != at1

    # Replay rejected.
    replay = await client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": rt1, "client_id": client_id},
    )
    assert replay.status_code == 400


# ---------------------------------------------------------------------------
# Authorizing-key fingerprint binding (router level).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_otp_endpoint_rejects_caller_without_fingerprint(app_with_oauth, client):
    """If the API-key manager has no fingerprint for the caller (e.g. ROOT or
    trusted-mode identity), OTP issuance must refuse — there's no key whose
    lifetime we could bind the resulting OAuth tokens to."""
    app, _, _ = app_with_oauth
    # Swap in an empty fingerprint map so get_user_key_fingerprint returns None
    # for the fixture's (acct1, alice) identity.
    app.state.api_key_manager = _FpOnlyKeyManager({})
    resp = await client.post("/api/v1/auth/otp", json={})
    assert resp.status_code == 400, resp.text
    body = resp.json()
    msg = body.get("error_description") or body.get("message") or ""
    assert "registered API key" in msg


@pytest.mark.asyncio
async def test_oauth_verify_rejects_verifier_without_fingerprint(app_with_oauth, client):
    """oauth-verify must refuse to bind OAuth state to an identity that has
    no fingerprint — same invariant as OTP issuance."""
    app, store, _ = app_with_oauth
    _, pending_id, _, _ = await _start_authorize(client, redirect_uri="https://x.test/cb")
    code = (await store.load_pending_authorization(pending_id))["display_code"]

    app.state.api_key_manager = _FpOnlyKeyManager({})  # verifier has no key
    resp = await client.post(
        "/api/v1/auth/oauth-verify",
        json={"code": code, "decision": "approve"},
    )
    assert resp.status_code == 400
    msg = resp.json().get("error_description") or resp.json().get("message") or ""
    assert "registered API key" in msg


@pytest.mark.asyncio
async def test_oauth_token_carries_authorizing_key_fp(app_with_oauth, client):
    """Happy path: every minted token records the verifier's fp; rotation of
    that fingerprint at the manager level invalidates the token at bearer
    auth time. Here we just verify the fp is correctly recorded — auth-time
    rejection has its own coverage in test_auth_integration."""
    app, store, provider = app_with_oauth
    expected_fp = "f" * 64

    client_id, pending_id, _, verifier = await _start_authorize(
        client, redirect_uri="https://x.test/cb"
    )
    code = (await store.load_pending_authorization(pending_id))["display_code"]

    await client.post("/api/v1/auth/oauth-verify", json={"code": code, "decision": "approve"})
    pending = await store.load_pending_authorization(pending_id)
    # After verify, the pending row carries the verifier's fp.
    assert pending["verified_key_fp"] == expected_fp

    status = await client.get("/oauth/authorize/page/status", params={"pending": pending_id})
    auth_code = status.json()["redirect_url"].split("code=")[1].split("&")[0]
    token_resp = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://x.test/cb",
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    access_token = token_resp.json()["access_token"]
    record = await provider.load_access_token(access_token)
    assert record is not None
    assert record.authorizing_key_fp == expected_fp
    refresh_record = await store.peek_refresh(token_resp.json()["refresh_token"])
    assert refresh_record["authorizing_key_fp"] == expected_fp
