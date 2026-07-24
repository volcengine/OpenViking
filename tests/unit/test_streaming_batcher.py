# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.session.memory.utils.streaming_batcher import (
    StreamingBatcher,
    StreamingBatcherConfig,
)


@pytest.mark.asyncio
async def test_close_waits_for_in_flight_timer_flush():
    started = asyncio.Event()
    finish = asyncio.Event()

    async def process_batch(items: list[str], _reason: str) -> str:
        started.set()
        await finish.wait()
        return ",".join(items)

    batcher = StreamingBatcher(
        name="test",
        process_batch=process_batch,
        config=StreamingBatcherConfig(
            max_items_per_batch=10,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.001,
        ),
    )
    submit = asyncio.create_task(batcher.submit("item"))
    await asyncio.wait_for(started.wait(), timeout=1)

    close = asyncio.create_task(batcher.close())
    await asyncio.sleep(0)
    finish.set()

    assert await asyncio.wait_for(submit, timeout=1) == "item"
    assert await asyncio.wait_for(close, timeout=1) is None
