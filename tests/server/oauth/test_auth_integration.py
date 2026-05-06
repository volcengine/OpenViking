# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Integration tests for the JWT discriminator path in openviking/server/auth.py."""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from openviking.server.auth import resolve_identity
from openviking.server.config import ServerConfig
from openviking.server.identity import Role
from openviking.server.oauth.jwt import JwtSigner
from openviking_cli.exceptions import PermissionDeniedError, UnauthenticatedError


SECRET = b"k" * 32


def _make_request(
    *,
    bearer: Optional[str],
    api_key_manager,
    oauth_signer: Optional[JwtSigner],
    extra_headers: Optional[dict[str, str]] = None,
    path: str = "/api/v1/system/status",
) -> Request:
    raw_headers = []
    if bearer:
        raw_headers.append((b"authorization", f"Bearer {bearer}".encode()))
    for k, v in (extra_headers or {}).items():
        raw_headers.append((k.lower().encode(), v.encode()))
    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="api_key", root_api_key="root-test-1234567890abcd")
    app.state.api_key_manager = api_key_manager
    if oauth_signer is not None:
        app.state.oauth_signer = oauth_signer
    scope = {
        "type": "http",
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "app": app,
    }
    return Request(scope)


class _StubKeyManager:
    """Minimal APIKeyManager replacement that fails when called.

    Tests that exercise the JWT path want to assert the API-key path is
    *not* invoked; tests on backwards compat patch this stub to behave.
    """

    def __init__(self, raise_on_resolve: bool = True):
        self._raise = raise_on_resolve

    def resolve(self, key: str):  # noqa: D401
        if self._raise:
            raise AssertionError(f"API-key path should not be reached for {key!r}")
        from openviking.server.identity import ResolvedIdentity

        return ResolvedIdentity(role=Role.USER, account_id="api-acct", user_id="api-user")

    def get_account_policy(self, account_id):
        from openviking.server.identity import AccountNamespacePolicy

        return AccountNamespacePolicy()


@pytest.mark.asyncio
async def test_jwt_resolves_to_claims_identity():
    signer = JwtSigner(SECRET)
    token = signer.sign(
        {
            "iss": "https://ov.test",
            "role": "user",
            "account_id": "tenant-a",
            "user_id": "alice",
        },
        ttl_seconds=60,
    )
    request = _make_request(
        bearer=token,
        api_key_manager=_StubKeyManager(),  # would assert if invoked
        oauth_signer=signer,
    )
    identity = await resolve_identity(
        request,
        x_api_key=None,
        authorization=f"Bearer {token}",
    )
    assert identity.role == Role.USER
    assert identity.account_id == "tenant-a"
    assert identity.user_id == "alice"
    assert identity.from_oauth is True


@pytest.mark.asyncio
async def test_jwt_invalid_signature_fails_closed():
    """A token that LOOKS like a JWT but doesn't verify must NOT fall back to API key."""
    signer = JwtSigner(SECRET)
    token = signer.sign({"role": "user", "account_id": "a", "user_id": "u"}, ttl_seconds=60)
    # Tamper signature.
    head, payload, sig = token.split(".")
    tampered = f"{head}.{payload}.{sig[:-2]}AA"

    request = _make_request(
        bearer=tampered,
        # API-key manager would have been a fallback; raise_on_resolve=False
        # would let it succeed if reached. Test asserts it's NOT reached.
        api_key_manager=_StubKeyManager(raise_on_resolve=True),
        oauth_signer=signer,
    )
    with pytest.raises(UnauthenticatedError, match="Invalid OAuth token"):
        await resolve_identity(request, x_api_key=None, authorization=f"Bearer {tampered}")


@pytest.mark.asyncio
async def test_non_jwt_bearer_falls_through_to_api_key():
    """A plain API key (no JWT shape) must still resolve via APIKeyManager."""
    signer = JwtSigner(SECRET)
    plain_key = "ov_user_NOTAJWT_abcdefghijklmnop"
    request = _make_request(
        bearer=plain_key,
        api_key_manager=_StubKeyManager(raise_on_resolve=False),
        oauth_signer=signer,
    )
    identity = await resolve_identity(
        request, x_api_key=None, authorization=f"Bearer {plain_key}"
    )
    assert identity.role == Role.USER
    assert identity.account_id == "api-acct"
    assert identity.from_oauth is False


@pytest.mark.asyncio
async def test_jwt_path_skipped_when_oauth_disabled():
    """When no oauth_signer is on app.state, JWT-shaped tokens go to API key path."""
    signer = JwtSigner(SECRET)
    token = signer.sign({"role": "user", "account_id": "a", "user_id": "u"}, ttl_seconds=60)
    request = _make_request(
        bearer=token,
        api_key_manager=_StubKeyManager(raise_on_resolve=False),
        oauth_signer=None,  # OAuth disabled
    )
    identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
    # Falls through to API key: stub returns a fixed user identity.
    assert identity.from_oauth is False


@pytest.mark.asyncio
async def test_jwt_user_role_rejects_account_override():
    """A USER OAuth token cannot impersonate another tenant via header."""
    signer = JwtSigner(SECRET)
    token = signer.sign(
        {"role": "user", "account_id": "tenant-a", "user_id": "alice"}, ttl_seconds=60
    )
    request = _make_request(
        bearer=token,
        api_key_manager=_StubKeyManager(),
        oauth_signer=signer,
        extra_headers={"x-openviking-account": "tenant-b"},
    )
    with pytest.raises(PermissionDeniedError):
        await resolve_identity(
            request,
            x_api_key=None,
            authorization=f"Bearer {token}",
            x_openviking_account="tenant-b",
        )


@pytest.mark.asyncio
async def test_jwt_root_role_can_be_used_without_explicit_tenant_headers():
    """ROOT OAuth tokens carry account/user in claims — no header requirement."""
    signer = JwtSigner(SECRET)
    token = signer.sign(
        {"role": "root", "account_id": "tenant-a", "user_id": "alice"}, ttl_seconds=60
    )
    request = _make_request(
        bearer=token,
        api_key_manager=_StubKeyManager(),
        oauth_signer=signer,
    )
    identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
    assert identity.role == Role.ROOT
    # from_oauth must propagate so get_request_context skips the
    # ROOT-requires-explicit-tenant-headers guard.
    assert identity.from_oauth is True
