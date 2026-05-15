# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for concurrent lock acquisition using real AGFS backend."""

import asyncio
import uuid

from openviking.storage.transaction.lock_handle import LockHandle
from openviking.storage.transaction.path_lock import PathLockEngine


class TestConcurrentLock:
    async def test_exact_mutual_exclusion_same_path(self, agfs_client, test_dir):
        """两个任务竞争同一路径的 EXACT 锁，均最终成功（串行执行）。"""
        lock = PathLockEngine(agfs_client)

        results = {}

        async def holder(tx_id):
            tx = LockHandle(id=tx_id)
            ok = await lock.acquire_exact_path(test_dir, tx, timeout=5.0)
            if ok:
                await asyncio.sleep(0.3)
                await lock.release(tx)
            results[tx_id] = ok

        await asyncio.gather(
            holder("tx-conc-1"),
            holder("tx-conc-2"),
        )

        # Both should eventually succeed (one waits for the other)
        assert results["tx-conc-1"] is True
        assert results["tx-conc-2"] is True

    async def test_tree_blocks_concurrent_exact_child(self, agfs_client, test_dir):
        """TreeLock on parent 持锁期间，子目录的 ExactPathLock 被阻塞，释放后成功。"""
        child = f"{test_dir}/child-{uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        lock = PathLockEngine(agfs_client)
        parent_acquired = asyncio.Event()
        parent_released = asyncio.Event()

        child_result = {}

        async def parent_holder():
            tx = LockHandle(id="tx-tree-parent")
            ok = await lock.acquire_tree(test_dir, tx, timeout=5.0)
            assert ok is True
            parent_acquired.set()
            await asyncio.sleep(0.5)
            await lock.release(tx)
            parent_released.set()

        async def child_worker():
            await parent_acquired.wait()
            tx = LockHandle(id="tx-tree-child")
            ok = await lock.acquire_exact_path(child, tx, timeout=5.0)
            child_result["ok"] = ok
            child_result["after_release"] = parent_released.is_set()
            if ok:
                await lock.release(tx)

        await asyncio.gather(parent_holder(), child_worker())

        assert child_result["ok"] is True
        # Child should succeed only after parent released
        assert child_result["after_release"] is True

    async def test_exact_child_blocks_concurrent_tree_parent(self, agfs_client, test_dir):
        """ExactPathLock on child 持锁期间，父目录的 TreeLock 被阻塞，释放后成功。"""
        child = f"{test_dir}/child-{uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        lock = PathLockEngine(agfs_client)
        child_acquired = asyncio.Event()
        child_released = asyncio.Event()

        parent_result = {}

        async def child_holder():
            tx = LockHandle(id="tx-rev-child")
            ok = await lock.acquire_exact_path(child, tx, timeout=5.0)
            assert ok is True
            child_acquired.set()
            await asyncio.sleep(0.5)
            await lock.release(tx)
            child_released.set()

        async def parent_worker():
            await child_acquired.wait()
            tx = LockHandle(id="tx-rev-parent")
            ok = await lock.acquire_tree(test_dir, tx, timeout=5.0)
            parent_result["ok"] = ok
            parent_result["after_release"] = child_released.is_set()
            if ok:
                await lock.release(tx)

        await asyncio.gather(child_holder(), parent_worker())

        assert parent_result["ok"] is True
        assert parent_result["after_release"] is True
