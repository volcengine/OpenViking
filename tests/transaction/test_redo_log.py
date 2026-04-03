# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for RedoLog crash recovery."""

import uuid

import pytest

from openviking.storage.transaction.redo_log import RedoLog


@pytest.fixture
def redo(agfs_client):
    return RedoLog(agfs_client)


class TestRedoLogBasic:
    def test_write_and_read(self, redo):
        task_id = uuid.uuid4().hex
        info = {"archive_uri": "viking://test/archive", "session_uri": "viking://test/session"}
        redo.write_pending(task_id, info)

        result = redo.read(task_id)
        assert result["archive_uri"] == "viking://test/archive"
        assert result["session_uri"] == "viking://test/session"

        redo.mark_done(task_id)

    def test_list_pending(self, redo):
        t1 = uuid.uuid4().hex
        t2 = uuid.uuid4().hex
        redo.write_pending(t1, {"key": "v1"})
        redo.write_pending(t2, {"key": "v2"})

        pending = redo.list_pending()
        assert t1 in pending
        assert t2 in pending

        redo.mark_done(t1)
        pending_after = redo.list_pending()
        assert t1 not in pending_after
        assert t2 in pending_after

        redo.mark_done(t2)

    def test_mark_done_removes_task(self, redo):
        task_id = uuid.uuid4().hex
        redo.write_pending(task_id, {"x": 1})
        redo.mark_done(task_id)

        pending = redo.list_pending()
        assert task_id not in pending

    def test_read_nonexistent_returns_empty(self, redo):
        result = redo.read("nonexistent-task-id")
        assert result == {}

    def test_list_pending_empty(self, redo):
        # Should not crash even if _REDO_ROOT doesn't exist yet
        pending = redo.list_pending()
        assert isinstance(pending, list)

    def test_mark_done_idempotent(self, redo):
        task_id = uuid.uuid4().hex
        redo.write_pending(task_id, {"x": 1})
        redo.mark_done(task_id)
        # Second mark_done should not raise
        redo.mark_done(task_id)

    def test_overwrite_pending(self, redo):
        task_id = uuid.uuid4().hex
        redo.write_pending(task_id, {"version": 1})
        redo.write_pending(task_id, {"version": 2})

        result = redo.read(task_id)
        assert result["version"] == 2

        redo.mark_done(task_id)
