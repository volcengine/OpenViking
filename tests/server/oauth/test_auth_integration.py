# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Integration tests for the OAuth opaque-token discriminator in auth.py."""

from __future__ import annotations

from types import SimpleNamespace
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
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import UnauthenticatedError
from tests.server.account_store_fakes import InMemoryAGFS

_ROOT_KEY = ACCESS_TOKEN_PREFIX + "configured-root-key-1234567890abcd"


def _make_request(
    *,
    bearer: Optional[str],
    api_key_manager,
    oauth_provider: Optional[OpenVikingOAuthProvider],
    extra_headers: Optional[dict[str, str]] = None,
    path: str = "/api/v1/system/status",
) -> Request:
    from openviking.server.auth.plugins import ApiKeyAuthPlugin
    from openviking.server.auth.registry import get_registry

    raw_headers = []
    if bearer:
        raw_headers.append((b"authorization", f"Bearer {bearer}".encode()))
    for k, v in (extra_headers or {}).items():
        raw_headers.append((k.lower().encode(), v.encode()))
    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="api_key", root_api_key=_ROOT_KEY)
    app.state.api_key_manager = api_key_manager
    if oauth_provider is not None:
        app.state.oauth_provider = oauth_provider
    # Set auth plugin (lifespan not triggered in ASGI tests)
    registry = get_registry()
    if registry.get("api_key") is None:
        registry.register(ApiKeyAuthPlugin)
    app.state.auth_plugin = registry.get("api_key")()
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
    async def resolve_identity(self, key: str):
        raise AssertionError(f"API-key authentication should not be reached for {key!r}")

    async def get_user_key_fingerprint(self, account_id: str, user_id: str) -> str:
        return _TEST_FP


@pytest_asyncio.fixture
async def registered_key():
    manager = build_api_key_manager(_ROOT_KEY, SimpleNamespace(agfs=InMemoryAGFS()))
    await manager.load()
    try:
        await manager.create_account("tenant-a", "owner")
        key = await manager.register_user("tenant-a", "alice")
        yield manager, key
    finally:
        await manager.close()


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
async def test_unknown_oauth_token_fails_closed(provider):
    """A bearer with the OAuth prefix but unknown to the store must NOT fall back to API key."""
    bogus = ACCESS_TOKEN_PREFIX + "not-a-real-token"
    request = _make_request(
        bearer=bogus,
        api_key_manager=_StubKeyManager(),
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
        api_key_manager=_StubKeyManager(),
        oauth_provider=provider,
    )
    with pytest.raises(
        UnauthenticatedError, match="OAuth access token is invalid, expired, or revoked"
    ):
        await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")


@pytest.mark.asyncio
async def test_non_prefixed_bearer_falls_through_to_api_key(provider, registered_key):
    manager, plain_key = registered_key
    request = _make_request(bearer=plain_key, api_key_manager=manager, oauth_provider=provider)

    identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {plain_key}")

    assert (identity.role, identity.account_id, identity.user_id) == (
        Role.USER,
        "tenant-a",
        "alice",
    )
    assert identity.from_oauth is False


@pytest.mark.asyncio
async def test_oauth_path_skipped_when_disabled(registered_key):
    manager, _ = registered_key
    request = _make_request(bearer=_ROOT_KEY, api_key_manager=manager, oauth_provider=None)

    identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {_ROOT_KEY}")

    assert identity.role == Role.ROOT
    assert identity.from_oauth is False


@pytest.mark.asyncio
async def test_oauth_user_role_ignores_account_override(provider, store, registered_key):
    manager, _ = registered_key
    token = await _mint_token(
        provider,
        store,
        account_id="tenant-a",
        user_id="alice",
        role="user",
        authorizing_key_fp=await manager.get_user_key_fingerprint("tenant-a", "alice"),
    )
    request = _make_request(
        bearer=token,
        api_key_manager=manager,
        oauth_provider=provider,
        extra_headers={"x-openviking-account": "tenant-b", "x-openviking-user": "bob"},
    )

    identity = await resolve_identity(
        request,
        x_api_key=None,
        authorization=f"Bearer {token}",
        x_openviking_account="tenant-b",
        x_openviking_user="bob",
    )

    assert (identity.role, identity.account_id, identity.user_id) == (
        Role.USER,
        "tenant-a",
        "alice",
    )
    assert identity.from_oauth is True
    forwarded_headers = Request(request.scope).headers
    assert "x-openviking-account" not in forwarded_headers
    assert "x-openviking-user" not in forwarded_headers


# ---------------------------------------------------------------------------
# Lifecycle binding: OAuth lifetime ≤ authorizing key lifetime.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_token_follows_authorizing_key_lifetime(provider, store, registered_key):
    manager, _ = registered_key

    for change in ("rotate", "delete"):
        token = await _mint_token(
            provider,
            store,
            account_id="tenant-a",
            user_id="alice",
            role="user",
            authorizing_key_fp=await manager.get_user_key_fingerprint("tenant-a", "alice"),
        )
        request = _make_request(bearer=token, api_key_manager=manager, oauth_provider=provider)
        identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
        assert (identity.account_id, identity.user_id) == ("tenant-a", "alice")

        if change == "rotate":
            await manager.regenerate_key("tenant-a", "alice")
        else:
            await manager.begin_deletion(
                "tenant-a",
                "alice",
                task_id="delete-alice",
                owner_account_id="_system",
                owner_user_id="root",
            )

        with pytest.raises(UnauthenticatedError, match="rotated or revoked"):
            await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")


@pytest.mark.parametrize("initial,current", [(Role.ADMIN, Role.USER), (Role.USER, Role.ADMIN)])
async def test_oauth_token_respects_current_role(provider, store, initial, current):
    manager = build_api_key_manager("root", SimpleNamespace(agfs=InMemoryAGFS()))
    await manager.load()
    try:
        await manager.create_account("tenant-a", "owner")
        await manager.register_user("tenant-a", "alice", initial)
        token = await _mint_token(
            provider,
            store,
            account_id="tenant-a",
            user_id="alice",
            role=initial,
            authorizing_key_fp=await manager.get_user_key_fingerprint("tenant-a", "alice"),
        )
        request = _make_request(bearer=token, api_key_manager=manager, oauth_provider=provider)
        identity = await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
        assert identity.role == initial

        await manager.set_role("tenant-a", "alice", current)
        if current == Role.USER:
            with pytest.raises(UnauthenticatedError, match="exceeds the user's current role"):
                await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
        else:
            identity = await resolve_identity(
                request, x_api_key=None, authorization=f"Bearer {token}"
            )
            assert identity.role == Role.USER
    finally:
        await manager.close()


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
        api_key_manager=_StubKeyManager(),
        oauth_provider=provider,
    )
    with pytest.raises(UnauthenticatedError, match="rotated or revoked"):
        await resolve_identity(request, x_api_key=None, authorization=f"Bearer {token}")
