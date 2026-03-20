# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end lock tests using real AGFS backend.

These tests exercise LockContext -> LockManager -> PathLock -> AGFS,
verifying the acquire -> operate -> release lifecycle.
"""

import uuid

import pytest

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction.lock_context import LockContext
from openviking.storage.transaction.lock_manager import LockManager
from openviking.storage.transaction.path_lock import LOCK_FILE_NAME


def _lock_file_gone(agfs_client, lock_path: str) -> bool:
    """Return True if the lock file does not exist in AGFS."""
    try:
        agfs_client.stat(lock_path)
        return False
    except Exception:
        return True


@pytest.fixture
def lock_manager(agfs_client):
    return LockManager(agfs=agfs_client, lock_timeout=1.0, lock_expire=1.0)


class TestLockContextCommit:
    async def test_lock_acquired_and_released(self, agfs_client, lock_manager, test_dir):
        """Lock is held inside the context and released after exit."""
        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"

        async with LockContext(lock_manager, [test_dir], lock_mode="point"):
            token = agfs_client.cat(lock_path)
            assert token is not None

        assert _lock_file_gone(agfs_client, lock_path)

    async def test_file_persists_after_context(self, agfs_client, lock_manager, test_dir):
        """Files written inside a lock context persist."""
        file_path = f"{test_dir}/committed-file.txt"

        async with LockContext(lock_manager, [test_dir], lock_mode="point"):
            agfs_client.write(file_path, b"committed data")

        content = agfs_client.cat(file_path)
        assert content == b"committed data"


class TestLockContextException:
    async def test_lock_released_on_exception(self, agfs_client, lock_manager, test_dir):
        """Lock is released even when an exception occurs inside the context."""
        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"

        with pytest.raises(RuntimeError):
            async with LockContext(lock_manager, [test_dir], lock_mode="point"):
                token = agfs_client.cat(lock_path)
                assert token is not None
                raise RuntimeError("simulated failure")

        assert _lock_file_gone(agfs_client, lock_path)

    async def test_exception_not_swallowed(self, agfs_client, lock_manager, test_dir):
        """Exceptions propagate through the context manager."""
        with pytest.raises(ValueError, match="test error"):
            async with LockContext(lock_manager, [test_dir], lock_mode="point"):
                raise ValueError("test error")


class TestLockContextMv:
    async def test_mv_lock_acquires_both_paths(self, agfs_client, lock_manager, test_dir):
        """mv lock mode acquires SUBTREE on both source and destination."""
        src = f"{test_dir}/mv-src-{uuid.uuid4().hex}"
        dst = f"{test_dir}/mv-dst-{uuid.uuid4().hex}"
        agfs_client.mkdir(src)
        agfs_client.mkdir(dst)

        async with LockContext(lock_manager, [src], lock_mode="mv", mv_dst_parent_path=dst):
            src_token = agfs_client.cat(f"{src}/{LOCK_FILE_NAME}")
            dst_token = agfs_client.cat(f"{dst}/{LOCK_FILE_NAME}")
            src_token_str = src_token.decode("utf-8") if isinstance(src_token, bytes) else src_token
            dst_token_str = dst_token.decode("utf-8") if isinstance(dst_token, bytes) else dst_token
            assert ":S" in src_token_str
            assert ":S" in dst_token_str

        for path in [f"{src}/{LOCK_FILE_NAME}", f"{dst}/{LOCK_FILE_NAME}"]:
            assert _lock_file_gone(agfs_client, path)


class TestLockContextSubtree:
    async def test_subtree_lock_and_release(self, agfs_client, lock_manager, test_dir):
        """Subtree lock is acquired and released."""
        target = f"{test_dir}/sub-{uuid.uuid4().hex}"
        agfs_client.mkdir(target)

        async with LockContext(lock_manager, [target], lock_mode="subtree"):
            token = agfs_client.cat(f"{target}/{LOCK_FILE_NAME}")
            token_str = token.decode("utf-8") if isinstance(token, bytes) else token
            assert ":S" in token_str

        assert _lock_file_gone(agfs_client, f"{target}/{LOCK_FILE_NAME}")


class TestSequentialLocks:
    async def test_sequential_locks_on_same_path(self, agfs_client, lock_manager, test_dir):
        """Multiple sequential lock contexts on the same path succeed."""
        for i in range(3):
            async with LockContext(lock_manager, [test_dir], lock_mode="point"):
                agfs_client.write(f"{test_dir}/f{i}.txt", f"data-{i}".encode())

        for i in range(3):
            content = agfs_client.cat(f"{test_dir}/f{i}.txt")
            assert content == f"data-{i}".encode()

    async def test_lock_acquisition_failure(self, agfs_client, lock_manager, test_dir):
        """LockContext raises LockAcquisitionError for nonexistent path."""
        nonexistent = f"{test_dir}/nonexistent-{uuid.uuid4().hex}"
        with pytest.raises(LockAcquisitionError):
            async with LockContext(lock_manager, [nonexistent], lock_mode="point"):
                pass
