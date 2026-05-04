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
    token, expires_at = store.issue("acct", "user", "upload_x.md", ttl_seconds=60)
    assert len(token) == _TOKEN_LENGTH
    assert all(c in _TOKEN_ALPHABET for c in token)
    assert expires_at > 0


def test_consume_roundtrip(store):
    token, _ = store.issue("acct", "user", "upload_x.md", ttl_seconds=60)
    aid, uid = store.consume(token, "upload_x.md")
    assert (aid, uid) == ("acct", "user")


def test_consume_burns_token(store):
    token, _ = store.issue("acct", "user", "upload_x.md", ttl_seconds=60)
    store.consume(token, "upload_x.md")
    with pytest.raises(UploadTokenError, match="unknown or already-consumed"):
        store.consume(token, "upload_x.md")


def test_consume_unknown_token(store):
    with pytest.raises(UploadTokenError, match="unknown or already-consumed"):
        store.consume("ZZZZZZ", "upload_x.md")


def test_consume_missing_token(store):
    with pytest.raises(UploadTokenError, match="missing"):
        store.consume("", "upload_x.md")


def test_consume_wrong_temp_file_id(store):
    token, _ = store.issue("acct", "user", "upload_x.md", ttl_seconds=60)
    with pytest.raises(UploadTokenError, match="does not match"):
        store.consume(token, "upload_other.md")
    # Token is consumed even on tfid mismatch (defensive — no second-chance attacks)
    assert store.peek(token) is None


def test_consume_expired_token(store, monkeypatch):
    import openviking.server.upload_token_store as mod

    fake_now = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: fake_now[0])

    token, _ = store.issue("acct", "user", "upload_x.md", ttl_seconds=60)
    fake_now[0] += 61  # advance past expiry
    with pytest.raises(UploadTokenError, match="expired"):
        store.consume(token, "upload_x.md")


def test_purge_expired_drops_stale_tokens(store, monkeypatch):
    import openviking.server.upload_token_store as mod

    fake_now = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: fake_now[0])

    t1, _ = store.issue("a", "u", "f1", ttl_seconds=10)
    t2, _ = store.issue("a", "u", "f2", ttl_seconds=600)

    fake_now[0] += 30  # t1 expired, t2 still alive

    # Issuing a new token implicitly purges; t1 should be gone afterward
    store.issue("a", "u", "f3", ttl_seconds=600)
    assert store.peek(t1) is None
    assert store.peek(t2) is not None


def test_issue_handles_dense_alphabet_collisions(store, monkeypatch):
    """Force collisions to verify the retry loop still terminates."""
    import openviking.server.upload_token_store as mod

    # Pin secrets.choice so the first 7 picks always collide; 8th call should still succeed.
    call_count = [0]
    real_choice = mod.secrets.choice

    def fake_choice(seq):
        call_count[0] += 1
        # 6 calls per token attempt; force same chars for first 7 attempts (42 calls),
        # then real randomness afterward.
        if call_count[0] <= 42:
            return seq[0]
        return real_choice(seq)

    monkeypatch.setattr(mod.secrets, "choice", fake_choice)

    t1, _ = store.issue("a", "u", "f1", ttl_seconds=60)  # gets "AAAAAA"
    # Second issue collides 7 times before retry randomness kicks in
    t2, _ = store.issue("a", "u", "f2", ttl_seconds=60)
    assert t1 != t2


def test_clear_resets_state(store):
    store.issue("a", "u", "f", ttl_seconds=60)
    store.clear()
    assert store._store == {}
