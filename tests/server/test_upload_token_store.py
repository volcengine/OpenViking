# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for openviking/server/upload_token_store.py."""

from __future__ import annotations

import pytest

from openviking.server.upload_token_store import (
    _TOKEN_ALPHABET,
    _TOKEN_LENGTH,
    UploadTokenError,
    UploadTokenStore,
)


@pytest.fixture
def store() -> UploadTokenStore:
    return UploadTokenStore()


def test_issue_returns_token_of_expected_length(store):
    token, expires_at = store.issue("acct", "user", "agent", ttl_seconds=60)
    assert len(token) == _TOKEN_LENGTH
    assert all(c in _TOKEN_ALPHABET for c in token)
    assert expires_at > 0


def test_consume_roundtrip(store):
    token, _ = store.issue("acct", "user", "agent", ttl_seconds=60)
    aid, uid, agid = store.consume(token)
    assert (aid, uid, agid) == ("acct", "user", "agent")


def test_consume_burns_token(store):
    token, _ = store.issue("acct", "user", "agent", ttl_seconds=60)
    store.consume(token)
    with pytest.raises(UploadTokenError, match="unknown or already-consumed"):
        store.consume(token)


def test_consume_unknown_token(store):
    with pytest.raises(UploadTokenError, match="unknown or already-consumed"):
        store.consume("ZZZZZZ")


def test_consume_missing_token(store):
    with pytest.raises(UploadTokenError, match="missing"):
        store.consume("")


def test_consume_expired_token(store, monkeypatch):
    import openviking.server.upload_token_store as mod

    fake_now = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: fake_now[0])

    token, _ = store.issue("acct", "user", "agent", ttl_seconds=60)
    fake_now[0] += 61
    with pytest.raises(UploadTokenError, match="expired"):
        store.consume(token)


def test_purge_expired_drops_stale_tokens(store, monkeypatch):
    import openviking.server.upload_token_store as mod

    fake_now = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: fake_now[0])

    t1, _ = store.issue("a", "u", "ag", ttl_seconds=10)
    t2, _ = store.issue("a", "u", "ag", ttl_seconds=600)

    fake_now[0] += 30  # t1 expired, t2 still alive

    # Issuing a new token implicitly purges; t1 should be gone afterward
    store.issue("a", "u", "ag", ttl_seconds=600)
    assert store.peek(t1) is None
    assert store.peek(t2) is not None


def test_issue_handles_dense_alphabet_collisions(store, monkeypatch):
    """Force collisions to verify the retry loop still terminates."""
    import openviking.server.upload_token_store as mod

    call_count = [0]
    real_choice = mod.secrets.choice

    def fake_choice(seq):
        call_count[0] += 1
        if call_count[0] <= 42:
            return seq[0]
        return real_choice(seq)

    monkeypatch.setattr(mod.secrets, "choice", fake_choice)

    t1, _ = store.issue("a", "u", "ag", ttl_seconds=60)
    t2, _ = store.issue("a", "u", "ag", ttl_seconds=60)
    assert t1 != t2


def test_clear_resets_state(store):
    store.issue("a", "u", "ag", ttl_seconds=60)
    store.clear()
    assert store._store == {}
