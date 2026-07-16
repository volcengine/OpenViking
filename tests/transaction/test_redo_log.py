# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for RedoLog crash recovery."""


import pytest

from openviking.storage.transaction.redo_log import RedoLog


@pytest.fixture
def redo(agfs_client):
    return RedoLog(agfs_client)


class TestRedoLogBasic:
    async def test_read_nonexistent_returns_empty(self, redo):
        result = await redo.read_async("nonexistent-task-id")
        assert result == {}

    async def test_list_pending_empty(self, redo):
        # Should not crash even if _REDO_ROOT doesn't exist yet
        pending = await redo.list_pending_async()
        assert isinstance(pending, list)
