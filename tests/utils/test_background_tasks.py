# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""A fire-and-forget task must outlive the garbage collector.

The event loop keeps only a weak reference to a running task. A task launched by
`asyncio.create_task(...)` whose return value is discarded therefore has no
strong reference at all, and CPython is free to collect it mid-await. Every
caller in this repository that launched work this way had already written a
tracker row and returned its id, so a collected task leaves a job that a client
polls forever.
"""

import asyncio
import gc

import pytest

from openviking.utils.background_tasks import pending_background_tasks, spawn_background_task


async def test_task_survives_a_collection_while_it_is_awaiting():
    """The regression itself, forced rather than waited for.

    Both halves run the same coroutine and the same `gc.collect()`. The only
    difference is whether a strong reference exists, which is the whole claim.
    """
    started = asyncio.Event()
    finished = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        finished.set()

    task = spawn_background_task(work(), name="survivor")
    await started.wait()

    gc.collect()

    assert task in pending_background_tasks()
    await asyncio.wait_for(finished.wait(), timeout=1)
    assert finished.is_set()


async def test_reference_is_released_once_the_task_finishes():
    """The set must not become a leak: a finished task is dropped."""

    async def work() -> None:
        await asyncio.sleep(0)

    task = spawn_background_task(work(), name="transient")
    assert task in pending_background_tasks()

    await task
    # done callbacks run on the next loop iteration
    await asyncio.sleep(0)

    assert task not in pending_background_tasks()


async def test_a_failing_task_is_logged_and_still_released(caplog):
    """An exception must not be swallowed silently nor pin the reference."""

    async def boom() -> None:
        raise RuntimeError("background boom")

    with caplog.at_level("ERROR"):
        task = spawn_background_task(boom(), name="boom")
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

    assert task not in pending_background_tasks()
    assert any("background boom" in record.getMessage() for record in caplog.records)


async def test_cancellation_is_not_reported_as_a_failure(caplog):
    """Shutdown cancels in-flight work; that is not an error to log."""

    async def forever() -> None:
        await asyncio.sleep(3600)

    with caplog.at_level("ERROR"):
        task = spawn_background_task(forever(), name="cancelled")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    assert task not in pending_background_tasks()
    assert not [record for record in caplog.records if "cancelled" in record.getMessage()]
