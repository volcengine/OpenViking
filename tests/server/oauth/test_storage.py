# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for openviking/server/oauth/storage.py."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from openviking.server.oauth import storage
from openviking.server.oauth.storage import OAuthStore

# Test stand-in for an API key fingerprint. Real fps come from
# APIKeyManager.get_user_key_fingerprint() — sha256 hex, 64 chars.
_FP = "a" * 64
_FP_OTHER = "b" * 64


@pytest_asyncio.fixture
async def store(tmp_path):
    s = OAuthStore(tmp_path / "oauth.db")
    await s.initialize()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def clock(monkeypatch):
    now = SimpleNamespace(seconds=1_000)
    monkeypatch.setattr(storage, "time", SimpleNamespace(time=lambda: now.seconds))
    return now


@pytest.mark.asyncio
async def test_refresh_path_role_downgrade_rejected(store):
    """Provider's exchange_refresh_token must refuse to mint new tokens
    when the user's role has been downgraded since the refresh was issued.
    Mirrors the bearer-auth role check; covered here because the refresh
    path lives in provider.py, not in the storage layer."""
    from mcp.server.auth.provider import TokenError
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    from openviking.server.identity import Role
    from openviking.server.oauth.provider import OpenVikingOAuthProvider

    provider = OpenVikingOAuthProvider(
        store=store,
        issuer="https://ov.test",
        role_resolver=lambda _account, _user: Role.USER,
    )
    await store.register_client(client_id="cx", redirect_uris=["https://x.test/cb"])
    token = "rt-admin-era"
    await store.insert_refresh(
        token_plain=token,
        client_id="cx",
        account_id="acct",
        user_id="alice",
        role="admin",
        scope=None,
        resource=None,
        authorizing_key_fp=_FP,
        ttl_seconds=86400,
    )
    client = OAuthClientInformationFull(client_id="cx", redirect_uris=[AnyUrl("https://x.test/cb")])
    refresh = await provider.load_refresh_token(client, token)
    assert refresh is not None
    with pytest.raises(TokenError) as exc_info:
        await provider.exchange_refresh_token(client, refresh, scopes=[])
    assert exc_info.value.error == "invalid_grant"
    assert "downgraded" in (exc_info.value.error_description or "")
    assert await store.peek_refresh(token) is not None


async def test_provider_get_client_keeps_registered_scope(store):
    from openviking.server.oauth.provider import OpenVikingOAuthProvider

    callback = "https://claude.ai/api/mcp/auth_callback"
    record = await store.register_client(
        redirect_uris=[callback], client_name="Claude.ai", scope="claudeai"
    )
    provider = OpenVikingOAuthProvider(store=store, issuer="https://ov.test")

    client = await provider.get_client(record["client_id"])
    assert client is not None
    assert client.client_id == record["client_id"]
    assert client.redirect_uris is not None
    assert [str(uri) for uri in client.redirect_uris] == [callback]
    assert client.client_name == "Claude.ai"
    assert client.token_endpoint_auth_method == "none"
    assert "authorization_code" in client.grant_types
    assert client.scope == "claudeai"
    assert client.validate_scope("claudeai") == ["claudeai"]
    assert await provider.get_client("unregistered-client") is None


def _insert_code(store, code, *, account_id="a", user_id="u", ttl_seconds=300):
    return store.insert_auth_code(
        code_plain=code,
        client_id="cx",
        redirect_uri="https://x.test/cb",
        code_challenge="ch",
        code_challenge_method="S256",
        scope=None,
        resource=None,
        account_id=account_id,
        user_id=user_id,
        role="user",
        authorizing_key_fp=_FP,
        ttl_seconds=ttl_seconds,
    )


async def test_auth_code_concurrent_consume_race(store, tmp_path):
    code = "race-code"
    claims = {
        "client_id": "cx",
        "redirect_uri": "https://x.test/cb",
        "code_challenge": "challenge-xyz",
        "code_challenge_method": "S256",
        "scope": "mcp",
        "resource": "https://x.test/mcp",
        "account_id": "a",
        "user_id": "u",
        "role": "user",
        "authorizing_key_fp": _FP,
    }
    await store.insert_auth_code(code_plain=code, ttl_seconds=300, **claims)
    second_store = OAuthStore(tmp_path / "oauth.db")
    await second_store.initialize()
    try:
        results = await asyncio.gather(
            store.consume_auth_code(code), second_store.consume_auth_code(code)
        )
        [winner] = [record for record in results if record is not None]
        assert {field: winner[field] for field in claims} == claims
        assert await second_store.peek_auth_code(code) is None
    finally:
        await second_store.close()


async def test_access_token_load_and_revoke(store, clock):
    claims = {
        "client_id": "cx",
        "account_id": "acct",
        "user_id": "alice",
        "role": "user",
        "scope": "mcp",
        "resource": "https://ov.test/mcp",
        "authorizing_key_fp": _FP,
    }
    await store.insert_access(token_plain="at-expiring", ttl_seconds=60, **claims)
    await store.insert_access(token_plain="at-revoked", ttl_seconds=3600, **claims)
    for token in ("at-expiring", "at-revoked"):
        record = await store.load_access(token)
        assert record is not None
        assert {field: record[field] for field in claims} == claims

    clock.seconds += 60
    assert await store.load_access("at-expiring") is None
    assert await store.revoke_access("at-revoked") is True
    assert await store.load_access("at-revoked") is None
    assert await store.revoke_access("at-revoked") is False


@pytest.mark.asyncio
async def test_revoke_tokens_cascades(store):
    """Revoking a user wipes their access, refresh, and unused codes."""
    await store.insert_access(
        token_plain="at-1",
        client_id="cx",
        account_id="acct",
        user_id="alice",
        role="user",
        scope=None,
        resource=None,
        authorizing_key_fp=_FP,
        ttl_seconds=3600,
    )
    await store.insert_access(
        token_plain="at-other",
        client_id="cx",
        account_id="acct",
        user_id="bob",  # different user — must NOT be revoked
        role="user",
        scope=None,
        resource=None,
        authorizing_key_fp=_FP_OTHER,
        ttl_seconds=3600,
    )
    await store.insert_refresh(
        token_plain="rt-1",
        client_id="cx",
        account_id="acct",
        user_id="alice",
        role="user",
        scope=None,
        resource=None,
        authorizing_key_fp=_FP,
        ttl_seconds=3600,
    )
    await _insert_code(store, "code-alice", account_id="acct", user_id="alice")
    alice_pending = await store.create_pending_authorization(
        client_id="cx",
        redirect_uri="https://example/cb",
        redirect_uri_provided_explicitly=True,
        code_challenge="chal",
        scopes=None,
        resource=None,
        state=None,
        display_code="ALICE-1",
    )
    assert await store.mark_pending_verified(
        pending_id=alice_pending,
        account_id="acct",
        user_id="alice",
        role="user",
        verified_key_fp=_FP,
    )
    bob_pending = await store.create_pending_authorization(
        client_id="cx",
        redirect_uri="https://example/cb",
        redirect_uri_provided_explicitly=True,
        code_challenge="chal",
        scopes=None,
        resource=None,
        state=None,
        display_code="BOB-1",
    )
    assert await store.mark_pending_verified(
        pending_id=bob_pending,
        account_id="acct",
        user_id="bob",
        role="user",
        verified_key_fp=_FP_OTHER,
    )

    counts = await store.revoke_tokens(account_id="acct", user_id="alice")
    assert counts["access_tokens_revoked"] == 1
    assert counts["refresh_tokens_revoked"] == 1
    assert counts["codes_revoked"] == 1
    assert counts["pending_authorizations_revoked"] == 1

    # Alice's everything dead, Bob's untouched.
    assert await store.load_access("at-1") is None
    assert await store.load_access("at-other") is not None
    assert await store.peek_refresh("rt-1") is None
    assert await store.consume_auth_code("code-alice") is None
    assert await store.load_pending_authorization(alice_pending) is None
    assert await store.load_pending_authorization(bob_pending) is not None

    account_counts = await store.revoke_tokens(account_id="acct")
    assert account_counts["access_tokens_revoked"] == 1
    assert account_counts["pending_authorizations_revoked"] == 1
    assert await store.load_access("at-other") is None
    assert await store.load_pending_authorization(bob_pending) is None


@pytest.mark.asyncio
async def test_gc_keeps_refresh_tombstones_until_natural_expiry(store, clock):
    """RFC 9700 §4.14 replay detection needs consumed-but-unexpired tombstones
    to stick around — only after the original expires_at can we GC them."""
    rt = "rt-tomb"
    await store.insert_refresh(
        token_plain=rt,
        client_id="cx",
        account_id="acct",
        user_id="alice",
        role="user",
        scope=None,
        resource=None,
        authorizing_key_fp=_FP,
        ttl_seconds=86400,  # 1 day, well beyond GC window
    )
    # Consume (rotate) — row now has consumed=1 but is still within expires_at.
    consumed = await store.consume_refresh(token_plain=rt, replaced_by_plain="rt-next")
    assert consumed is not None
    assert consumed["client_id"] == "cx"
    assert consumed["authorizing_key_fp"] == _FP
    assert await store.consume_refresh(token_plain=rt, replaced_by_plain=None) is None
    # GC must NOT delete the tombstone yet.
    await store.gc_expired()
    assert await store.is_refresh_known_but_consumed(rt) is True
    clock.seconds += 86401
    await store.gc_expired()
    assert await store.is_refresh_known_but_consumed(rt) is False  # row truly gone now


async def test_gc_expired_removes_stale_rows(store, clock):
    await _insert_code(store, "fresh", ttl_seconds=300)
    await _insert_code(store, "stale", ttl_seconds=60)

    clock.seconds += 60
    assert await store.peek_auth_code("stale") is None
    assert await store.consume_auth_code("stale") is None
    clock.seconds += 1
    assert (await store.gc_expired())["codes_deleted"] == 1
    assert await store.consume_auth_code("fresh") is not None
