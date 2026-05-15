# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for LockContext async context manager."""

import uuid

import pytest

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction.lock_context import LockContext
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


class TestLockContextExact:
    async def test_exact_lock_lifecycle(self, agfs_client, lm, test_dir):
        async with LockContext(lm, [test_dir], lock_mode="exact") as handle:
            assert handle is not None
            lock_path = handle.locks[0]
            token = agfs_client.cat(lock_path)
            assert token is not None

        assert _lock_file_gone(agfs_client, lock_path)

    async def test_lock_released_on_exception(self, agfs_client, lm, test_dir):
        lock_path = ""

        with pytest.raises(RuntimeError):
            async with LockContext(lm, [test_dir], lock_mode="exact") as handle:
                lock_path = handle.locks[0]
                assert agfs_client.cat(lock_path) is not None
                raise RuntimeError("fail")

        assert lock_path
        assert _lock_file_gone(agfs_client, lock_path)

    async def test_exception_propagates(self, lm, test_dir):
        with pytest.raises(ValueError, match="test"):
            async with LockContext(lm, [test_dir], lock_mode="exact"):
                raise ValueError("test")


class TestLockContextTree:
    async def test_tree_lock(self, agfs_client, lm, test_dir):
        async with LockContext(lm, [test_dir], lock_mode="tree"):
            token = agfs_client.cat(f"{test_dir}/{LOCK_FILE_NAME}")
            token_str = token.decode("utf-8") if isinstance(token, bytes) else token
            assert ":T" in token_str


class TestLockContextMv:
    async def test_mv_lock(self, agfs_client, lm, test_dir):
        src = f"{test_dir}/src-{uuid.uuid4().hex}"
        dst = f"{test_dir}/dst-{uuid.uuid4().hex}"
        agfs_client.mkdir(src)
        agfs_client.mkdir(dst)

        async with LockContext(lm, [src], lock_mode="mv", mv_dst_path=dst) as handle:
            assert len(handle.locks) == 2


class TestLockContextFailure:
    async def test_unsupported_lock_mode_raises(self, lm):
        with pytest.raises(LockAcquisitionError):
            async with LockContext(lm, ["/local/nonexistent-xyz"], lock_mode="unknown"):
                pass

    async def test_handle_cleaned_up_on_failure(self, lm):
        with pytest.raises(LockAcquisitionError):
            async with LockContext(lm, ["/local/nonexistent-xyz"], lock_mode="unknown"):
                pass

        assert len(lm.get_active_handles()) == 0


class TestLockContextExternalHandle:
    async def test_external_handle_reuses_existing_tree_lock(self, agfs_client, lm, test_dir):
        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"

        async with LockContext(lm, [test_dir], lock_mode="tree") as handle:
            before = agfs_client.cat(lock_path)
            before_token = before.decode("utf-8") if isinstance(before, bytes) else before
            assert ":T" in before_token

            async with LockContext(lm, [test_dir], lock_mode="exact", handle=handle):
                current = agfs_client.cat(lock_path)
                current_token = current.decode("utf-8") if isinstance(current, bytes) else current
                assert current_token == before_token
                assert ":T" in current_token

            still_owned = agfs_client.cat(lock_path)
            still_owned_token = (
                still_owned.decode("utf-8") if isinstance(still_owned, bytes) else still_owned
            )
            assert still_owned_token == before_token

        assert _lock_file_gone(agfs_client, lock_path)
