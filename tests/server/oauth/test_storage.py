# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for openviking/server/oauth/storage.py."""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from openviking.server.oauth.otp import hash_secret
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


@pytest.mark.asyncio
async def test_refresh_path_role_downgrade_rejected():
    """Provider's exchange_refresh_token must refuse to mint new tokens
    when the user's role has been downgraded since the refresh was issued.
    Mirrors the bearer-auth role check; covered here because the refresh
    path lives in provider.py, not in the storage layer."""
    import tempfile
    from pathlib import Path

    from mcp.server.auth.provider import TokenError
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    from openviking.server.identity import Role
    from openviking.server.oauth.provider import OpenVikingOAuthProvider, OVRefreshToken

    with tempfile.TemporaryDirectory() as tmpdir:
        store = OAuthStore(Path(tmpdir) / "oauth.db")
        await store.initialize()
        try:
            # role_resolver reports current role as USER (post-demotion).
            current_roles = {("acct", "alice"): Role.USER}
            provider = OpenVikingOAuthProvider(
                store=store,
                issuer="https://ov.test",
                role_resolver=lambda a, u: current_roles.get((a, u), Role.USER),
            )
            await store.register_client(
                client_id="cx",
                redirect_uris=["https://x.test/cb"],
            )
            rt = "rt-admin-era"
            await store.insert_refresh(
                token_plain=rt,
                client_id="cx",
                account_id="acct",
                user_id="alice",
                role="admin",  # token issued when alice was admin
                scope=None,
                resource=None,
                authorizing_key_fp="f" * 64,
                ttl_seconds=86400,
            )
            client = OAuthClientInformationFull(
                client_id="cx",
                redirect_uris=[AnyUrl("https://x.test/cb")],
            )
            refresh_obj = OVRefreshToken(
                token=rt,
                client_id="cx",
                scopes=[],
                expires_at=None,
                account_id="acct",
                user_id="alice",
                role="admin",
                resource=None,
                authorizing_key_fp="f" * 64,
            )
            with pytest.raises(TokenError) as exc_info:
                await provider.exchange_refresh_token(client, refresh_obj, scopes=[])
            assert exc_info.value.error == "invalid_grant"
            assert "downgraded" in (exc_info.value.error_description or "")
        finally:
            await store.close()


@pytest.mark.asyncio
async def test_register_and_get_client(store):
    record = await store.register_client(
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        client_name="Claude.ai",
        scope="mcp",
    )
    assert record["client_id"]
    assert record["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
    assert record["token_endpoint_auth_method"] == "none"
    assert record["scope"] == "mcp"
    fetched = await store.get_client(record["client_id"])
    assert fetched is not None
    assert fetched["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
    assert fetched["client_name"] == "Claude.ai"
    assert fetched["scope"] == "mcp"
    assert "authorization_code" in fetched["grant_types"]


@pytest.mark.asyncio
async def test_get_client_missing(store):
    assert await store.get_client("nope") is None


@pytest.mark.asyncio
async def test_provider_get_client_defaults_missing_scope(store):
    """A client registered without a scope (ChatGPT's DCR omits the field;
    rows predating #2921 are NULL) must still pass validate_scope for the
    "mcp" scope the PRM document advertises. #2921 persisted the scope when
    the registrar sent one; this covers the scope-less registration."""
    from openviking.server.oauth.provider import MCP_SCOPE, OpenVikingOAuthProvider

    record = await store.register_client(
        redirect_uris=["https://chatgpt.com/connector/oauth/cb"],
        client_name="ChatGPT",
        scope=None,
    )
    provider = OpenVikingOAuthProvider(store=store, issuer="https://ov.test")
    client = await provider.get_client(record["client_id"])
    assert client is not None
    assert client.scope == MCP_SCOPE
    # The exact request that failed in the field: /authorize?scope=mcp.
    assert client.validate_scope(MCP_SCOPE) == [MCP_SCOPE]


@pytest.mark.asyncio
async def test_provider_get_client_keeps_registered_scope(store):
    """The missing-scope fallback must not clobber an explicitly registered
    scope (e.g. Claude registers its own scope string via DCR)."""
    from openviking.server.oauth.provider import OpenVikingOAuthProvider

    record = await store.register_client(
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        client_name="Claude.ai",
        scope="claudeai",
    )
    provider = OpenVikingOAuthProvider(store=store, issuer="https://ov.test")
    client = await provider.get_client(record["client_id"])
    assert client is not None
    assert client.scope == "claudeai"
    assert client.validate_scope("claudeai") == ["claudeai"]


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


@pytest.mark.asyncio
async def test_auth_code_concurrent_consume_race(store):
    """Two coroutines racing to consume the same code — exactly one wins."""
    code = "race-code"
    await _insert_code(store, code)
    results = await asyncio.gather(store.consume_auth_code(code), store.consume_auth_code(code))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


@pytest.mark.asyncio
async def test_auth_code_expired_rejected(store):
    code = "stale-code"
    await _insert_code(store, code, ttl_seconds=60)
    # Forge expiry by editing the row to be in the past.
    assert store._conn is not None
    store._conn.execute(
        "UPDATE oauth_codes SET expires_at = ? WHERE used = 0",
        (int(time.time()) - 10,),
    )
    assert await store.consume_auth_code(code) is None


@pytest.mark.asyncio
async def test_auth_code_roundtrip(store):
    code = "code-secret"
    await store.insert_auth_code(
        code_plain=code,
        client_id="client-x",
        redirect_uri="https://example.com/cb",
        code_challenge="challenge-xyz",
        code_challenge_method="S256",
        scope="mcp",
        resource="https://example.com/mcp",
        account_id="a",
        user_id="u",
        role="user",
        authorizing_key_fp=_FP,
        ttl_seconds=300,
    )
    record = await store.consume_auth_code(code)
    assert record is not None
    assert record["client_id"] == "client-x"
    assert record["redirect_uri"] == "https://example.com/cb"
    assert record["code_challenge"] == "challenge-xyz"
    assert record["scope"] == "mcp"
    assert record["resource"] == "https://example.com/mcp"
    assert record["authorizing_key_fp"] == _FP
    # Second consume rejected
    assert await store.consume_auth_code(code) is None


@pytest.mark.asyncio
async def test_refresh_token_roundtrip(store):
    rt = "rt-secret-1"
    await store.insert_refresh(
        token_plain=rt,
        client_id="client-x",
        account_id="a",
        user_id="u",
        role="user",
        scope="mcp",
        resource=None,
        authorizing_key_fp=_FP,
        ttl_seconds=86400,
    )
    record = await store.consume_refresh(token_plain=rt, replaced_by_plain="rt-secret-2")
    assert record is not None
    assert record["client_id"] == "client-x"
    assert record["authorizing_key_fp"] == _FP
    # Reuse detection: second use returns None but the row is still flagged consumed.
    assert await store.consume_refresh(token_plain=rt, replaced_by_plain=None) is None
    assert await store.is_refresh_known_but_consumed(rt) is True


@pytest.mark.asyncio
async def test_refresh_replay_revokes_chain(store):
    """Reusing a consumed refresh must allow the caller to revoke the family."""
    rt1 = "rt-1"
    rt2 = "rt-2"
    rt3 = "rt-3"
    for rt in (rt1, rt2, rt3):
        await store.insert_refresh(
            token_plain=rt,
            client_id="cx",
            account_id="acct",
            user_id="user",
            role="user",
            scope=None,
            resource=None,
            authorizing_key_fp=_FP,
            ttl_seconds=86400,
        )
    # Consume rt1 (rotate to rt2). Then attacker replays rt1.
    assert await store.consume_refresh(token_plain=rt1, replaced_by_plain=rt2) is not None
    assert await store.consume_refresh(token_plain=rt1, replaced_by_plain=None) is None
    # Detection — caller now revokes the chain.
    revoked = await store.revoke_chain(client_id="cx", account_id="acct", user_id="user")
    assert revoked >= 2  # rt2 and rt3 still active before revoke
    # Both rt2 and rt3 must now be unusable.
    assert await store.consume_refresh(token_plain=rt2, replaced_by_plain=None) is None
    assert await store.consume_refresh(token_plain=rt3, replaced_by_plain=None) is None


@pytest.mark.asyncio
async def test_access_token_load_and_revoke(store):
    token = "at-secret"
    await store.insert_access(
        token_plain=token,
        client_id="cx",
        account_id="acct",
        user_id="alice",
        role="user",
        scope="mcp",
        resource="https://ov.test/mcp",
        authorizing_key_fp=_FP,
        ttl_seconds=3600,
    )
    record = await store.load_access(token)
    assert record is not None
    assert record["account_id"] == "acct"
    assert record["user_id"] == "alice"
    assert record["scope"] == "mcp"
    assert record["resource"] == "https://ov.test/mcp"
    assert record["authorizing_key_fp"] == _FP
    # Revoke and confirm it's invisible.
    assert await store.revoke_access(token) is True
    assert await store.load_access(token) is None
    # Idempotent revoke.
    assert await store.revoke_access(token) is False


@pytest.mark.asyncio
async def test_access_token_expired_invisible(store):
    token = "at-stale"
    await store.insert_access(
        token_plain=token,
        client_id="cx",
        account_id="acct",
        user_id="alice",
        role="user",
        scope=None,
        resource=None,
        authorizing_key_fp=_FP,
        ttl_seconds=60,
    )
    assert store._conn is not None
    store._conn.execute(
        "UPDATE oauth_access_tokens SET expires_at = ? WHERE token_hash = ?",
        (int(time.time()) - 10, hash_secret(token)),
    )
    assert await store.load_access(token) is None


@pytest.mark.asyncio
async def test_revoke_user_tokens_cascades(store):
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

    counts = await store.revoke_user_tokens(account_id="acct", user_id="alice")
    assert counts["access_tokens_revoked"] == 1
    assert counts["refresh_tokens_revoked"] == 1
    assert counts["codes_revoked"] == 1

    # Alice's everything dead, Bob's untouched.
    assert await store.load_access("at-1") is None
    assert await store.load_access("at-other") is not None
    assert await store.consume_auth_code("code-alice") is None


@pytest.mark.asyncio
async def test_gc_keeps_refresh_tombstones_until_natural_expiry(store):
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
    # GC must NOT delete the tombstone yet.
    await store.gc_expired()
    assert await store.is_refresh_known_but_consumed(rt) is True
    # Backdate to past expiry — only now should GC reap it.
    assert store._conn is not None
    store._conn.execute(
        "UPDATE oauth_refresh_tokens SET expires_at = ? WHERE token_hash = ?",
        (int(time.time()) - 10, hash_secret(rt)),
    )
    await store.gc_expired()
    assert await store.is_refresh_known_but_consumed(rt) is False  # row truly gone now


@pytest.mark.asyncio
async def test_gc_expired_removes_stale_rows(store):
    fresh = "fresh-code"
    stale = "stale-code"
    await _insert_code(store, fresh)
    await _insert_code(store, stale)
    # Backdate stale row
    assert store._conn is not None
    store._conn.execute(
        "UPDATE oauth_codes SET expires_at = ? WHERE code_hash = ?",
        (int(time.time()) - 100, hash_secret(stale)),
    )
    deleted = await store.gc_expired()
    assert deleted["codes_deleted"] >= 1
    # Fresh row still consumable.
    assert await store.consume_auth_code(fresh) is not None
