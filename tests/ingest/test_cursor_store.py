# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CursorStore persistence / resume / accumulation."""

from contextlib import closing

from openviking.ingest.cursor_store import CursorStore
from openviking.ingest.models import BYTE_OFFSET, Cursor


def test_confirmed_batches_and_commit_survive_reopen(tmp_path):
    key = ("claude_code", "s1")
    session_id = "import__claude_code__s1"
    initial = Cursor.zero(BYTE_OFFSET)
    first = Cursor(BYTE_OFFSET, {"offset": 10, "inode": 1})
    second = Cursor(BYTE_OFFSET, {"offset": 42, "inode": 1})

    with closing(CursorStore(tmp_path)) as store:
        store.set_pending(*key, session_id, initial, first, 3, 0, locator="/log.jsonl")
        store.confirm_append(*key, first, 3)
        store.set_pending(*key, session_id, first, second, 2, 3)
        assert store.get_cursor(*key, BYTE_OFFSET) == first

    with closing(CursorStore(tmp_path)) as store:
        pending = store.get(*key)
        assert pending is not None
        assert pending.cursor == first
        assert pending.pending_cursor == second
        assert pending.pending_baseline == pending.last_appended_count == 3
        assert pending.pending_count == 2
        assert pending.locator == "/log.jsonl"
        assert pending.ov_session_id == session_id
        assert pending.needs_commit
        assert pending.last_committed_at is None

        store.confirm_append(*key, second, 2)
        store.mark_committed(*key)

    with closing(CursorStore(tmp_path)) as store:
        committed = store.get(*key)
        assert committed is not None
        assert committed.cursor == second
        assert committed.last_appended_count == 5
        assert committed.pending_cursor is None
        assert committed.pending_baseline == committed.pending_count == 0
        assert not committed.needs_commit
        assert committed.last_committed_at is not None
