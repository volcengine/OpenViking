# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for `PathLockEngine.acquire_mv` lock rollback (issue #1047).

When a move runs under a borrowed transaction handle (e.g. the outer subtree
TREE lock held by ``SemanticLockScope`` and shared by every move in
``_sync_topdown_recursive``), a *partial* mv lock failure must NOT tear down the
owner's pre-existing locks. The original code called ``self.release(owner)`` on
destination failure, which dropped the borrowed outer lock and cascaded into
"Failed to acquire mv lock" for every sibling move — leaving L0/L1 files stuck
in temp.

The fix rolls back only the locks acquired by the failing ``acquire_mv`` call.
"""

from __future__ import annotations

import pytest

from openviking.storage.transaction.lock_manager import LockManager
from openviking.storage.transaction.path_lock import LOCK_FILE_NAME, PathLockEngine
from tests.transaction.test_lock_reentrancy_unit import _FakeAGFS


@pytest.mark.asyncio
async def test_acquire_mv_dir_dst_conflict_preserves_borrowed_outer_lock():
    """src_is_dir=True: dst taken by another owner must not drop the outer lock."""
    agfs = _FakeAGFS()
    agfs.mkdir("/root")
    agfs.mkdir("/root/src")
    agfs.mkdir("/dest")
    agfs.mkdir("/dest/taken")
    lock = PathLockEngine(agfs)
    lm = LockManager(agfs)
    owner = lm.create_handle()
    other = lm.create_handle()

    # Owner holds the outer TREE lock on /root (its semantic subtree).
    assert await lock.acquire_tree("/root", owner, timeout=0.1) is True
    outer_lock_path = f"/root/{LOCK_FILE_NAME}"
    assert outer_lock_path in owner.locks

    # A concurrent task holds the mv destination, so the dst step will fail.
    assert await lock.acquire_exact_path("/dest/taken", other, timeout=0.1) is True

    # The move fails, but the borrowed outer lock must survive.
    acquired = await lock.acquire_mv(
        "/root/src", "/dest/taken", owner, timeout=0.0, src_is_dir=True
    )
    assert acquired is False
    assert outer_lock_path in owner.locks, "borrowed outer TREE lock was released"
    assert outer_lock_path in agfs._files, "outer TREE lock file was deleted from disk"


@pytest.mark.asyncio
async def test_acquire_mv_file_dst_conflict_preserves_borrowed_outer_lock():
    """src_is_dir=False: same guarantee on the file (exact/exact) path."""
    agfs = _FakeAGFS()
    agfs.mkdir("/root")
    agfs.write("/root/src.md", b"x")
    agfs.mkdir("/dest")
    agfs.write("/dest/taken.md", b"y")
    lock = PathLockEngine(agfs)
    lm = LockManager(agfs)
    owner = lm.create_handle()
    other = lm.create_handle()

    assert await lock.acquire_tree("/root", owner, timeout=0.1) is True
    outer_lock_path = f"/root/{LOCK_FILE_NAME}"

    # Hold the destination so the second exact-lock step fails.
    assert await lock.acquire_exact_path("/dest/taken.md", other, timeout=0.1) is True

    acquired = await lock.acquire_mv(
        "/root/src.md", "/dest/taken.md", owner, timeout=0.0, src_is_dir=False
    )
    assert acquired is False
    assert outer_lock_path in owner.locks, "borrowed outer TREE lock was released"
    assert outer_lock_path in agfs._files, "outer TREE lock file was deleted from disk"


@pytest.mark.asyncio
async def test_acquire_mv_success_still_locks_src_and_dst():
    """Happy path is unchanged: a clean mv acquires both src and dst locks."""
    agfs = _FakeAGFS()
    agfs.mkdir("/root")
    agfs.mkdir("/root/src")
    agfs.mkdir("/dest")
    lock = PathLockEngine(agfs)
    lm = LockManager(agfs)
    owner = lm.create_handle()

    acquired = await lock.acquire_mv(
        "/root/src", "/dest/moved", owner, timeout=0.1, src_is_dir=True
    )
    assert acquired is True
    # src gets a TREE lock; dst gets an exact-path lock (distinct file naming).
    assert f"/root/src/{LOCK_FILE_NAME}" in owner.locks
    assert any("moved" in lock_path for lock_path in owner.locks), owner.locks


@pytest.mark.asyncio
async def test_acquire_mv_dst_conflict_rolls_back_its_own_src_lock():
    """The failing call must not leak the src lock it acquired this round."""
    agfs = _FakeAGFS()
    agfs.mkdir("/area")
    agfs.mkdir("/area/src")
    agfs.mkdir("/area/dest")
    agfs.mkdir("/area/dest/taken")
    lock = PathLockEngine(agfs)
    lm = LockManager(agfs)
    owner = lm.create_handle()
    other = lm.create_handle()

    # No outer lock this time; owner starts empty.
    assert await lock.acquire_exact_path("/area/dest/taken", other, timeout=0.1) is True

    acquired = await lock.acquire_mv(
        "/area/src", "/area/dest/taken", owner, timeout=0.0, src_is_dir=True
    )
    assert acquired is False
    # The src TREE lock grabbed during this call must be rolled back.
    assert owner.locks == [], f"acquire_mv leaked locks: {owner.locks}"
    assert f"/area/src/{LOCK_FILE_NAME}" not in agfs._files
