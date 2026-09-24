# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reproduce contention between OV's commit and embedding queue event loops."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from openviking.storage.memory_trigger_index import MemoryTriggerIndex
from openviking_cli.utils.config.memory_trigger_config import MemoryTriggerConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def test_trigger_slots_work_after_contention_in_another_queue_loop():
    primary = SimpleNamespace(
        _config=VectorDBBackendConfig(dimension=4), collection_name="context", acl_manager=None
    )
    with patch("openviking.storage.memory_trigger_index.TriggerBackend"):
        index = MemoryTriggerIndex(primary, MemoryTriggerConfig(enabled=True, max_concurrent=1))

    async def contend():
        entered = asyncio.Event()

        async def waiter():
            async with index.model_slots:
                entered.set()

        async with index.model_slots:
            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.01)
            assert not entered.is_set()
        await asyncio.wait_for(task, 1)
        assert entered.is_set()

    # Ordinary asyncio.Semaphore binds to the first loop on contention and fails
    # in the second. Production uses separate loops for Commit and Embedding.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, contend()).result(timeout=3)
        pool.submit(asyncio.run, contend()).result(timeout=3)
