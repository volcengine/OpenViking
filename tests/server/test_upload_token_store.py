# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for openviking/server/upload_token_store.py."""

from __future__ import annotations

import pytest

from openviking.server.identity import Role
from openviking.server.upload_token_store import (
    UploadTokenError,
    UploadTokenStore,
)


@pytest.fixture
def store() -> UploadTokenStore:
    return UploadTokenStore()


def test_consume_burns_token(store):
    token, _ = store.issue("acct", "user", role=Role.USER, ttl_seconds=60)
    store.consume(token)
    with pytest.raises(UploadTokenError, match="unknown or already-consumed"):
        store.consume(token)


def test_consume_expired_token(store, monkeypatch):
    import openviking.server.upload_token_store as mod

    fake_now = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: fake_now[0])

    token, _ = store.issue("acct", "user", role=Role.USER, ttl_seconds=60)
    fake_now[0] += 61
    with pytest.raises(UploadTokenError, match="expired"):
        store.consume(token)


def test_purge_expired_drops_stale_tokens(store, monkeypatch):
    import openviking.server.upload_token_store as mod

    fake_now = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: fake_now[0])

    t1, _ = store.issue("a", "u", role=Role.USER, ttl_seconds=10)
    t2, _ = store.issue("a", "u", role=Role.USER, ttl_seconds=600)

    fake_now[0] += 30  # t1 expired, t2 still alive

    # Issuing a new token implicitly purges; t1 should be gone afterward
    store.issue("a", "u", role=Role.USER, ttl_seconds=600)
    assert store.peek(t1) is None
    assert store.peek(t2) is not None
