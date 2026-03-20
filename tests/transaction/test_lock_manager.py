# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for LockManager."""

import uuid

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
    async def test_create_handle_and_acquire_point(self, agfs_client, lm, test_dir):
        handle = lm.create_handle()
        ok = await lm.acquire_point(handle, test_dir)
        assert ok is True

        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"
        content = agfs_client.cat(lock_path)
        assert content is not None

        await lm.release(handle)
        assert _lock_file_gone(agfs_client, lock_path)

    async def test_acquire_subtree(self, agfs_client, lm, test_dir):
        handle = lm.create_handle()
        ok = await lm.acquire_subtree(handle, test_dir)
        assert ok is True

        token = agfs_client.cat(f"{test_dir}/{LOCK_FILE_NAME}")
        token_str = token.decode("utf-8") if isinstance(token, bytes) else token
        assert ":S" in token_str

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
        assert handle.id in lm.get_active_handles()

        await lm.acquire_point(handle, test_dir)
        await lm.release(handle)

        assert handle.id not in lm.get_active_handles()

    async def test_stop_releases_all(self, agfs_client, lm, test_dir):
        h1 = lm.create_handle()
        h2 = lm.create_handle()
        await lm.acquire_point(h1, test_dir)

        sub = f"{test_dir}/sub-{uuid.uuid4().hex}"
        agfs_client.mkdir(sub)
        await lm.acquire_point(h2, sub)

        await lm.stop()
        assert len(lm.get_active_handles()) == 0

    async def test_nonexistent_path_fails(self, lm):
        handle = lm.create_handle()
        ok = await lm.acquire_point(handle, "/local/nonexistent-xyz")
        assert ok is False
