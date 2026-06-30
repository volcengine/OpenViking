# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CursorStore persistence / resume / accumulation."""

from openviking.ingest.cursor_store import CursorStore
from openviking.ingest.models import BYTE_OFFSET, Cursor


def test_upsert_accumulate_and_resume(tmp_path):
    store = CursorStore(tmp_path)
    c1 = Cursor(BYTE_OFFSET, {"offset": 10, "inode": 1})
    store.upsert("claude_code", "s1", "import__claude_code__s1", c1, appended_delta=3, locator="/x")
    rec = store.get("claude_code", "s1")
    assert rec.cursor.value == {"offset": 10, "inode": 1}
    assert rec.last_appended_count == 3
    assert rec.ov_session_id == "import__claude_code__s1"
    assert rec.last_committed_at is None

    # advance + accumulate
    c2 = Cursor(BYTE_OFFSET, {"offset": 42, "inode": 1})
    store.upsert("claude_code", "s1", "import__claude_code__s1", c2, appended_delta=2)
    rec = store.get("claude_code", "s1")
    assert rec.cursor.value["offset"] == 42
    assert rec.last_appended_count == 5  # 3 + 2
    assert rec.locator == "/x"  # preserved across upsert
    store.close()

    # resume from a fresh store on the same dir
    store2 = CursorStore(tmp_path)
    rec2 = store2.get("claude_code", "s1")
    assert rec2.cursor.value["offset"] == 42
    assert rec2.last_appended_count == 5
    store2.close()


def test_commit_marks_timestamp(tmp_path):
    store = CursorStore(tmp_path)
    c = Cursor(BYTE_OFFSET, {"offset": 1})
    store.upsert("codex", "s", "ov", c, appended_delta=1)
    assert store.get("codex", "s").last_committed_at is None
    store.upsert("codex", "s", "ov", c, pending_tokens=0, committed=True)
    assert store.get("codex", "s").last_committed_at is not None
    store.close()
