# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Integration tests for the OAuth opaque-token discriminator in auth.py."""

from __future__ import annotations

from typing import Optional

import pytest
import pytest_asyncio
from fastapi import FastAPI
from starlette.requests import Request

from openviking.server.auth import resolve_identity
from openviking.server.config import ServerConfig
from openviking.server.identity import Role
from openviking.server.oauth.provider import (
    ACCESS_TOKEN_PREFIX,
    OpenVikingOAuthProvider,
)
from openviking.server.oauth.storage import OAuthStore
from openviking_cli.exceptions import PermissionDeniedError, UnauthenticatedError


def _make_request(
    *,
    bearer: Optional[str],
    api_key_manager,
    oauth_provider: Optional[OpenVikingOAuthProvider],
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
    if oauth_provider is not None:
        app.state.oauth_provider = oauth_provider
    scope = {
        "type": "http",
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "app": app,
    }
    return Request(scope)


_TEST_FP = "f" * 64  # stand-in sha256 fingerprint shared across tests


class _StubKeyManager:
    """API-key manager that asserts when reached unexpectedly.

    Tests for the OAuth path want to assert API-key fallback is *not* taken;
    backwards-compat tests flip ``raise_on_resolve`` to False.

    ``fingerprints`` maps (account_id, user_id) → fp. Defaults to a permissive
    map that returns ``_TEST_FP`` for any user, so existing tests don't have
    to know about the binding. Tests that exercise rotation / deletion pass
    a sparser map.
    """

    def __init__(
        self,
        raise_on_resolve: bool = True,
        fingerprints: Optional[dict[tuple[str, str], str]] = None,
        default_fingerprint: Optional[str] = _TEST_FP,
        roles: Optional[dict[tuple[str, str], Role]] = None,
        default_role: Role = Role.USER,
    ):
        self._raise = raise_on_resolve
        self._fps = fingerprints
        self._default_fp = default_fingerprint
        self._roles = roles
        self._default_role = default_role

    def resolve(self, key: str):  # noqa: D401
        if self._raise:
            raise AssertionError(f"API-key path should not be reached for {key!r}")
        from openviking.server.identity import ResolvedIdentity

        return ResolvedIdentity(role=Role.USER, account_id="api-acct", user_id="api-user")

    def get_account_policy(self, account_id):
        from openviking.server.identity import AccountNamespacePolicy

        return AccountNamespacePolicy()

    def get_user_key_fingerprint(self, account_id: str, user_id: str) -> Optional[str]:
        if self._fps is None:
            return self._default_fp
        return self._fps.get((account_id, user_id))

    def get_user_role(self, account_id: str, user_id: str) -> Role:
        if self._roles is None:
            return self._default_role
        return self._roles.get((account_id, user_id), self._default_role)


@pytest_asyncio.fixture
async def store(tmp_path):
    s = OAuthStore(tmp_path / "oauth.db")
    await s.initialize()
    try:
        yield s
    finally:
        await s.close()


@pytest_asyncio.fixture
async def provider(store):
    return OpenVikingOAuthProvider(store=store, issuer="https://ov.test")


async def _mint_token(provider: OpenVikingOAuthProvider, store: OAuthStore, **identity) -> str:
    """Helper: directly insert an access token bound to the given identity."""
    token = provider._mint_access()
    await store.insert_access(
        token_plain=token,
        client_id="test-client",
        account_id=identity["account_id"],
        user_id=identity["user_id"],
        role=identity["role"],
        scope=identity.get("scope"),
        resource=identity.get("resource"),
        authorizing_key_fp=identity.get("authorizing_key_fp", _TEST_FP),
        ttl_seconds=3600,
    )
    return token


@pytest.mark.asyncio
async def test_oauth_token_resolves_to_bound_identity(provider, store):
    token = await _mint_token(provider, store, account_id="tenant-a", user_id="alice", role="user")
    request = _make_request(
        bearer=token,
        api_key_manager=_StubKeyManager(),
        oauth_provider=provider,
    )
    identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
    assert identity.role == Role.USER
    assert identity.account_id == "tenant-a"
    assert identity.user_id == "alice"
    assert identity.from_oauth is True


@pytest.mark.asyncio
async def test_unknown_oauth_token_fails_closed(provider):
    """A bearer with the OAuth prefix but unknown to the store must NOT fall back to API key."""
    bogus = ACCESS_TOKEN_PREFIX + "not-a-real-token"
    request = _make_request(
        bearer=bogus,
        api_key_manager=_StubKeyManager(raise_on_resolve=True),
        oauth_provider=provider,
    )
    with pytest.raises(UnauthenticatedError, match="OAuth access token"):
        await resolve_identity(request, x_api_key=None, authorization=f"Bearer {bogus}")


@pytest.mark.asyncio
async def test_revoked_oauth_token_rejected(provider, store):
    token = await _mint_token(provider, store, account_id="acct", user_id="alice", role="user")
    await store.revoke_access(token)
    request = _make_request(
        bearer=token,
        api_key_manager=_StubKeyManager(raise_on_resolve=True),
        oauth_provider=provider,
    )
    with pytest.raises(UnauthenticatedError):
        await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")


@pytest.mark.asyncio
async def test_non_prefixed_bearer_falls_through_to_api_key(provider):
    """A plain API key (no OAuth prefix) must still resolve via APIKeyManager."""
    plain_key = "ov_user_NOTAOATAH_abcdefghijklmnop"
    request = _make_request(
        bearer=plain_key,
        api_key_manager=_StubKeyManager(raise_on_resolve=False),
        oauth_provider=provider,
    )
    identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {plain_key}")
    assert identity.role == Role.USER
    assert identity.account_id == "api-acct"
    assert identity.from_oauth is False


@pytest.mark.asyncio
async def test_oauth_path_skipped_when_disabled(store):
    """If no oauth_provider on app.state, prefixed tokens go to API-key path."""
    plain_key = ACCESS_TOKEN_PREFIX + "anything"
    request = _make_request(
        bearer=plain_key,
        api_key_manager=_StubKeyManager(raise_on_resolve=False),
        oauth_provider=None,
    )
    identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {plain_key}")
    # Falls through to API key; stub returns USER identity.
    assert identity.from_oauth is False


@pytest.mark.asyncio
async def test_oauth_user_role_rejects_account_override(provider, store):
    """A USER OAuth token cannot impersonate another tenant via header."""
    token = await _mint_token(provider, store, account_id="tenant-a", user_id="alice", role="user")
    request = _make_request(
        bearer=token,
        api_key_manager=_StubKeyManager(),
        oauth_provider=provider,
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
async def test_oauth_root_can_be_used_without_explicit_tenant_headers(provider, store):
    """ROOT OAuth tokens carry account/user in claims — no header requirement."""
    token = await _mint_token(provider, store, account_id="tenant-a", user_id="alice", role="root")
    # Manager must report alice as ROOT so the role-downgrade gate passes.
    request = _make_request(
        bearer=token,
        api_key_manager=_StubKeyManager(default_role=Role.ROOT),
        oauth_provider=provider,
    )
    identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
    assert identity.role == Role.ROOT
    assert identity.from_oauth is True


# ---------------------------------------------------------------------------
# Lifecycle binding: OAuth lifetime ≤ authorizing key lifetime.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_token_rejected_when_authorizing_key_rotated(provider, store):
    """Rotating the user's API key must invalidate every derived OAuth token."""
    token = await _mint_token(
        provider,
        store,
        account_id="tenant-a",
        user_id="alice",
        role="user",
        authorizing_key_fp=_TEST_FP,
    )
    # Simulate rotation: the manager now reports a different fingerprint.
    rotated_manager = _StubKeyManager(
        raise_on_resolve=True,
        fingerprints={("tenant-a", "alice"): "9" * 64},
    )
    request = _make_request(
        bearer=token,
        api_key_manager=rotated_manager,
        oauth_provider=provider,
    )
    with pytest.raises(UnauthenticatedError, match="rotated or revoked"):
        await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")


@pytest.mark.asyncio
async def test_oauth_token_rejected_when_authorizing_user_removed(provider, store):
    """Removing the user (fp lookup → None) invalidates the token."""
    token = await _mint_token(
        provider,
        store,
        account_id="tenant-a",
        user_id="alice",
        role="user",
        authorizing_key_fp=_TEST_FP,
    )
    # Sparse map ⇒ no fp for (tenant-a, alice) ⇒ user is gone.
    removed_manager = _StubKeyManager(raise_on_resolve=True, fingerprints={})
    request = _make_request(
        bearer=token,
        api_key_manager=removed_manager,
        oauth_provider=provider,
    )
    with pytest.raises(UnauthenticatedError, match="rotated or revoked"):
        await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")


@pytest.mark.asyncio
async def test_oauth_token_rejected_after_role_downgrade(provider, store):
    """set_role demotion must invalidate OAuth tokens whose embedded role
    outranks the user's current role — fp alone won't catch this because
    the key value is unchanged."""
    token = await _mint_token(
        provider,
        store,
        account_id="tenant-a",
        user_id="alice",
        role="admin",  # token issued when alice was admin
        authorizing_key_fp=_TEST_FP,
    )
    # Manager now reports alice as USER (post-set_role demotion).
    demoted_manager = _StubKeyManager(
        raise_on_resolve=True,
        roles={("tenant-a", "alice"): Role.USER},
    )
    request = _make_request(
        bearer=token,
        api_key_manager=demoted_manager,
        oauth_provider=provider,
    )
    with pytest.raises(UnauthenticatedError, match="exceeds the user's current role"):
        await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")


@pytest.mark.asyncio
async def test_oauth_token_survives_role_promotion(provider, store):
    """Promotion (current role > token role) is harmless — token's lower
    privilege is still authorized. Only downgrades trigger rejection."""
    token = await _mint_token(
        provider,
        store,
        account_id="tenant-a",
        user_id="alice",
        role="user",
        authorizing_key_fp=_TEST_FP,
    )
    promoted_manager = _StubKeyManager(
        raise_on_resolve=True,
        roles={("tenant-a", "alice"): Role.ADMIN},
    )
    request = _make_request(
        bearer=token,
        api_key_manager=promoted_manager,
        oauth_provider=provider,
    )
    identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
    # Token's USER role is preserved — promotion doesn't auto-elevate.
    assert identity.role == Role.USER


@pytest.mark.asyncio
async def test_oauth_token_without_recorded_fp_fails_closed(provider, store):
    """Tokens missing a recorded fingerprint (legacy / stub) must NOT validate."""
    token = await _mint_token(
        provider,
        store,
        account_id="tenant-a",
        user_id="alice",
        role="user",
        authorizing_key_fp="",  # writes empty fp into the row
    )
    request = _make_request(
        bearer=token,
        api_key_manager=_StubKeyManager(raise_on_resolve=True),
        oauth_provider=provider,
    )
    with pytest.raises(UnauthenticatedError, match="rotated or revoked"):
        await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
