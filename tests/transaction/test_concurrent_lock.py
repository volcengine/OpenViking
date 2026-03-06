# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for concurrent lock acquisition using real AGFS backend."""

import asyncio
import uuid

from openviking.storage.transaction.path_lock import PathLock
from openviking.storage.transaction.transaction_record import TransactionRecord


class TestConcurrentLock:
    async def test_point_mutual_exclusion_same_path(self, agfs_client, test_dir):
        """两个任务竞争同一路径的 POINT 锁，均最终成功（串行执行）。"""
        lock = PathLock(agfs_client)

        results = {}

        async def holder(tx_id):
            tx = TransactionRecord(id=tx_id)
            ok = await lock.acquire_point(test_dir, tx, timeout=5.0)
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

    async def test_subtree_blocks_concurrent_point_child(self, agfs_client, test_dir):
        """SUBTREE on parent 持锁期间，子目录的 POINT 被阻塞，释放后成功。"""
        child = f"{test_dir}/child-{uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        lock = PathLock(agfs_client)
        parent_acquired = asyncio.Event()
        parent_released = asyncio.Event()

        child_result = {}

        async def parent_holder():
            tx = TransactionRecord(id="tx-sub-parent")
            ok = await lock.acquire_subtree(test_dir, tx, timeout=5.0)
            assert ok is True
            parent_acquired.set()
            await asyncio.sleep(0.5)
            await lock.release(tx)
            parent_released.set()

        async def child_worker():
            await parent_acquired.wait()
            tx = TransactionRecord(id="tx-sub-child")
            ok = await lock.acquire_point(child, tx, timeout=5.0)
            child_result["ok"] = ok
            child_result["after_release"] = parent_released.is_set()
            if ok:
                await lock.release(tx)

        await asyncio.gather(parent_holder(), child_worker())

        assert child_result["ok"] is True
        # Child should succeed only after parent released
        assert child_result["after_release"] is True

    async def test_point_child_blocks_concurrent_subtree_parent(self, agfs_client, test_dir):
        """POINT on child 持锁期间，父目录的 SUBTREE 被阻塞，释放后成功。"""
        child = f"{test_dir}/child-{uuid.uuid4().hex}"
        agfs_client.mkdir(child)

        lock = PathLock(agfs_client)
        child_acquired = asyncio.Event()
        child_released = asyncio.Event()

        parent_result = {}

        async def child_holder():
            tx = TransactionRecord(id="tx-rev-child")
            ok = await lock.acquire_point(child, tx, timeout=5.0)
            assert ok is True
            child_acquired.set()
            await asyncio.sleep(0.5)
            await lock.release(tx)
            child_released.set()

        async def parent_worker():
            await child_acquired.wait()
            tx = TransactionRecord(id="tx-rev-parent")
            ok = await lock.acquire_subtree(test_dir, tx, timeout=5.0)
            parent_result["ok"] = ok
            parent_result["after_release"] = child_released.is_set()
            if ok:
                await lock.release(tx)

        await asyncio.gather(child_holder(), parent_worker())

        assert parent_result["ok"] is True
        assert parent_result["after_release"] is True
