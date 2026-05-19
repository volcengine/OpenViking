# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for LockManager."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.transaction.lock_manager import LockManager
from openviking.storage.transaction.path_lock import LOCK_FILE_NAME


def _lock_file_gone(agfs_client, lock_path: str) -> bool:
    try:
        agfs_client.stat(lock_path)
        return False
    except Exception:
        return True


@pytest.fixture
def lm(agfs_client):
    return LockManager(agfs=agfs_client, lock_timeout=1.0, lock_expire=1.0)


class TestLockManagerBasic:
    async def test_create_handle_and_acquire_exact_path(self, agfs_client, lm, test_dir):
        handle = lm.create_handle()
        ok = await lm.acquire_exact_path(handle, test_dir)
        assert ok is True

        lock_path = handle.locks[0]
        content = agfs_client.cat(lock_path)
        assert content is not None

        await lm.release(handle)
        assert _lock_file_gone(agfs_client, lock_path)

    async def test_acquire_tree(self, agfs_client, lm, test_dir):
        handle = lm.create_handle()
        ok = await lm.acquire_tree(handle, test_dir)
        assert ok is True

        token = agfs_client.cat(f"{test_dir}/{LOCK_FILE_NAME}")
        token_str = token.decode("utf-8") if isinstance(token, bytes) else token
        assert ":T" in token_str

        await lm.release(handle)

    async def test_acquire_mv(self, agfs_client, lm, test_dir):
        src = f"{test_dir}/mv-src-{uuid.uuid4().hex}"
        dst = f"{test_dir}/mv-dst-{uuid.uuid4().hex}"
        agfs_client.mkdir(src)
        agfs_client.mkdir(dst)

        handle = lm.create_handle()
        ok = await lm.acquire_mv(handle, src, dst)
        assert ok is True
        assert len(handle.locks) == 2

        await lm.release(handle)
        assert handle.id not in lm.get_active_handles()

    async def test_release_removes_from_active(self, lm, test_dir):
        handle = lm.create_handle()

        await lm.acquire_exact_path(handle, test_dir)
        assert handle.id in lm.get_active_handles()

        await lm.release(handle)

        assert handle.id not in lm.get_active_handles()

    async def test_stop_releases_all(self, agfs_client, lm, test_dir):
        h1 = lm.create_handle()
        h2 = lm.create_handle()
        await lm.acquire_exact_path(h1, test_dir)

        sub = f"{test_dir}/sub-{uuid.uuid4().hex}"
        agfs_client.mkdir(sub)
        await lm.acquire_exact_path(h2, sub)

        await lm.stop()
        assert len(lm.get_active_handles()) == 0

    async def test_exact_path_allows_missing_target(self, lm):
        handle = lm.create_handle()
        ok = await lm.acquire_exact_path(handle, "/local/nonexistent-xyz")
        assert ok is True

        await lm.release(handle)

    async def test_recover_pending_redo_preserves_cancelled_error(self, lm):
        lm._redo_log = MagicMock()
        lm._redo_log.list_pending.return_value = ["redo-task"]
        lm._redo_log.read.return_value = {"archive_uri": "a", "session_uri": "b"}
        lm._redo_session_memory = AsyncMock(side_effect=asyncio.CancelledError("shutdown"))

        with pytest.raises(asyncio.CancelledError):
            await lm._recover_pending_redo()

        lm._redo_log.mark_done.assert_not_called()

    async def test_start_skips_redo_recovery_when_disabled(self, client):
        lm_disabled = LockManager(
            agfs=client._client.service._agfs_client,
            lock_timeout=1.0,
            lock_expire=1.0,
            redo_recovery_enabled=False,
        )
        lm_disabled._recover_pending_redo = AsyncMock()

        await lm_disabled.start()
        await asyncio.sleep(0)

        assert lm_disabled._redo_task is None
        lm_disabled._recover_pending_redo.assert_not_called()

        await lm_disabled.stop()
