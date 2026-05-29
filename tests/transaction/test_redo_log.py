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
    async def test_write_and_read(self, redo):
        task_id = uuid.uuid4().hex
        info = {"archive_uri": "viking://test/archive", "session_uri": "viking://test/session"}
        await redo.write_pending_async(task_id, info)

        result = await redo.read_async(task_id)
        assert result["archive_uri"] == "viking://test/archive"
        assert result["session_uri"] == "viking://test/session"

        await redo.mark_done_async(task_id)

    async def test_list_pending(self, redo):
        t1 = uuid.uuid4().hex
        t2 = uuid.uuid4().hex
        await redo.write_pending_async(t1, {"key": "v1"})
        await redo.write_pending_async(t2, {"key": "v2"})

        pending = await redo.list_pending_async()
        assert t1 in pending
        assert t2 in pending

        await redo.mark_done_async(t1)
        pending_after = await redo.list_pending_async()
        assert t1 not in pending_after
        assert t2 in pending_after

        await redo.mark_done_async(t2)

    async def test_mark_done_removes_task(self, redo):
        task_id = uuid.uuid4().hex
        await redo.write_pending_async(task_id, {"x": 1})
        await redo.mark_done_async(task_id)

        pending = await redo.list_pending_async()
        assert task_id not in pending

    async def test_read_nonexistent_returns_empty(self, redo):
        result = await redo.read_async("nonexistent-task-id")
        assert result == {}

    async def test_list_pending_empty(self, redo):
        # Should not crash even if _REDO_ROOT doesn't exist yet
        pending = await redo.list_pending_async()
        assert isinstance(pending, list)

    async def test_mark_done_idempotent(self, redo):
        task_id = uuid.uuid4().hex
        await redo.write_pending_async(task_id, {"x": 1})
        await redo.mark_done_async(task_id)
        # Second mark_done should not raise
        await redo.mark_done_async(task_id)

    async def test_overwrite_pending(self, redo):
        task_id = uuid.uuid4().hex
        await redo.write_pending_async(task_id, {"version": 1})
        await redo.write_pending_async(task_id, {"version": 2})

        result = await redo.read_async(task_id)
        assert result["version"] == 2

        await redo.mark_done_async(task_id)
