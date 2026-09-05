# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for threshold-driven auto-commit on Session.add_messages.

The auto-commit threshold (_auto_commit_threshold) and the pending_tokens
signal were both maintained by _append_messages but never connected, so
in-process sessions (HTTP/SDK add_messages path) never auto-committed —
only the offline ingest poller (maybe_commit_on_threshold) did. This left
real-time sessions accumulating without extraction, the root cause of the
low session→memory conversion rate. _maybe_auto_commit wires them.

These target the _maybe_auto_commit wiring on Session via __new__ to avoid
the heavy VikingFS/vectordb __init__ path — no running server needed.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.session.session import Session, SessionMeta


def _bare_session() -> Session:
    """A Session with only the fields _maybe_auto_commit reads."""
    s = Session.__new__(Session)
    s._auto_commit_threshold = 0
    s._meta = SessionMeta(
        session_id="test",
        created_at=0,
        updated_at=0,
        created_by_account_id="",
        created_by_user_id="",
        message_count=0,
        commit_count=0,
    )
    s.commit_async = AsyncMock(return_value={"status": "accepted"})
    return s


class TestAutoCommit:
    """Threshold-driven auto-commit wiring."""

    async def test_auto_commit_fires_when_threshold_crossed(self):
        """pending_tokens >= threshold schedules a background commit_async."""
        s = _bare_session()
        s._auto_commit_threshold = 100
        s._meta.pending_tokens = 150  # over threshold

        s._maybe_auto_commit()
        await asyncio.sleep(0)  # let the fire-and-forget task run

        assert s.commit_async.called

    async def test_auto_commit_skipped_below_threshold(self):
        """Below threshold, _maybe_auto_commit must not schedule a commit."""
        s = _bare_session()
        s._auto_commit_threshold = 999_999
        s._meta.pending_tokens = 10

        s._maybe_auto_commit()
        await asyncio.sleep(0)

        assert not s.commit_async.called

    async def test_auto_commit_disabled_when_threshold_zero(self):
        """threshold <= 0 disables auto-commit entirely."""
        s = _bare_session()
        s._auto_commit_threshold = 0
        s._meta.pending_tokens = 10_000

        s._maybe_auto_commit()
        await asyncio.sleep(0)

        assert not s.commit_async.called

    async def test_auto_commit_forwards_keep_recent_count(self):
        """keep_recent_count from session meta is forwarded to commit_async."""
        s = _bare_session()
        s._auto_commit_threshold = 1
        s._meta.pending_tokens = 10
        s._meta.keep_recent_count = 7

        s._maybe_auto_commit()
        await asyncio.sleep(0)

        assert s.commit_async.call_args.kwargs["keep_recent_count"] == 7

    async def test_auto_commit_equal_threshold_fires(self):
        """Boundary: pending_tokens == threshold should fire (>=)."""
        s = _bare_session()
        s._auto_commit_threshold = 50
        s._meta.pending_tokens = 50  # exactly at threshold

        s._maybe_auto_commit()
        await asyncio.sleep(0)

        assert s.commit_async.called
