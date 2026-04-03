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


class TestLockContextPoint:
    async def test_point_lock_lifecycle(self, agfs_client, lm, test_dir):
        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"

        async with LockContext(lm, [test_dir], lock_mode="point") as handle:
            assert handle is not None
            token = agfs_client.cat(lock_path)
            assert token is not None

        assert _lock_file_gone(agfs_client, lock_path)

    async def test_lock_released_on_exception(self, agfs_client, lm, test_dir):
        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"

        with pytest.raises(RuntimeError):
            async with LockContext(lm, [test_dir], lock_mode="point"):
                assert agfs_client.cat(lock_path) is not None
                raise RuntimeError("fail")

        assert _lock_file_gone(agfs_client, lock_path)

    async def test_exception_propagates(self, lm, test_dir):
        with pytest.raises(ValueError, match="test"):
            async with LockContext(lm, [test_dir], lock_mode="point"):
                raise ValueError("test")


class TestLockContextSubtree:
    async def test_subtree_lock(self, agfs_client, lm, test_dir):
        async with LockContext(lm, [test_dir], lock_mode="subtree"):
            token = agfs_client.cat(f"{test_dir}/{LOCK_FILE_NAME}")
            token_str = token.decode("utf-8") if isinstance(token, bytes) else token
            assert ":S" in token_str


class TestLockContextMv:
    async def test_mv_lock(self, agfs_client, lm, test_dir):
        src = f"{test_dir}/src-{uuid.uuid4().hex}"
        dst = f"{test_dir}/dst-{uuid.uuid4().hex}"
        agfs_client.mkdir(src)
        agfs_client.mkdir(dst)

        async with LockContext(lm, [src], lock_mode="mv", mv_dst_parent_path=dst) as handle:
            assert len(handle.locks) == 2


class TestLockContextFailure:
    async def test_nonexistent_path_raises(self, lm):
        with pytest.raises(LockAcquisitionError):
            async with LockContext(lm, ["/local/nonexistent-xyz"], lock_mode="point"):
                pass

    async def test_handle_cleaned_up_on_failure(self, lm):
        with pytest.raises(LockAcquisitionError):
            async with LockContext(lm, ["/local/nonexistent-xyz"], lock_mode="point"):
                pass

        assert len(lm.get_active_handles()) == 0


class TestLockContextExternalHandle:
    async def test_external_handle_reuses_existing_subtree_lock(self, agfs_client, lm, test_dir):
        lock_path = f"{test_dir}/{LOCK_FILE_NAME}"

        async with LockContext(lm, [test_dir], lock_mode="subtree") as handle:
            before = agfs_client.cat(lock_path)
            before_token = before.decode("utf-8") if isinstance(before, bytes) else before
            assert ":S" in before_token

            async with LockContext(lm, [test_dir], lock_mode="point", handle=handle):
                current = agfs_client.cat(lock_path)
                current_token = current.decode("utf-8") if isinstance(current, bytes) else current
                assert current_token == before_token
                assert ":S" in current_token

            still_owned = agfs_client.cat(lock_path)
            still_owned_token = (
                still_owned.decode("utf-8") if isinstance(still_owned, bytes) else still_owned
            )
            assert still_owned_token == before_token

        assert _lock_file_gone(agfs_client, lock_path)
