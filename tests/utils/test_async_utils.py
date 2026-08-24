# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for reusable asyncio concurrency helpers."""

import asyncio
import threading
from typing import Any

import pytest

from openviking.utils.async_utils import (
    AsyncConcurrencyLimiter,
    KeyedAsyncLockPool,
    OwnerLoopDispatcher,
    run_to_completion,
)


@pytest.mark.asyncio
async def test_owner_loop_dispatcher_runs_foreign_loop_work_on_owner_loop():
    dispatcher = OwnerLoopDispatcher()
    dispatcher.bind_current_loop()
    owner_loop = asyncio.get_running_loop()
    work_loop: asyncio.AbstractEventLoop | None = None

    async def work() -> str:
        nonlocal work_loop
        work_loop = asyncio.get_running_loop()
        return "done"

    def run_from_foreign_loop() -> str:
        return asyncio.run(dispatcher.run(work))

    result = await asyncio.to_thread(run_from_foreign_loop)

    assert result == "done"
    assert work_loop is owner_loop


@pytest.mark.asyncio
async def test_owner_loop_dispatcher_propagates_foreign_cancellation():
    dispatcher = OwnerLoopDispatcher()
    dispatcher.bind_current_loop()
    work_started = asyncio.Event()
    work_cancelled = asyncio.Event()
    foreign_ready = threading.Event()
    cancel_foreign = threading.Event()

    async def work() -> None:
        work_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            work_cancelled.set()
            raise

    def run_from_foreign_loop() -> None:
        async def invoke() -> None:
            task = asyncio.create_task(dispatcher.run(work))
            foreign_ready.set()
            await asyncio.to_thread(cancel_foreign.wait)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(invoke())

    foreign_call = asyncio.create_task(asyncio.to_thread(run_from_foreign_loop))
    await work_started.wait()
    await asyncio.to_thread(foreign_ready.wait)
    cancel_foreign.set()
    await foreign_call
    await asyncio.wait_for(work_cancelled.wait(), timeout=1)


def test_owner_loop_dispatcher_rejects_calls_after_owner_loop_closes():
    dispatcher = OwnerLoopDispatcher()

    async def bind() -> None:
        dispatcher.bind_current_loop()

    asyncio.run(bind())

    async def invoke() -> None:
        with pytest.raises(RuntimeError, match="owner event loop is closed"):
            await dispatcher.run(lambda: asyncio.sleep(0))

    asyncio.run(invoke())


def test_owner_loop_dispatcher_rejects_stopped_owner_loop():
    dispatcher = OwnerLoopDispatcher()
    owner_loop = asyncio.new_event_loop()
    owner_loop.run_until_complete(asyncio.sleep(0))
    owner_loop.run_until_complete(_bind_dispatcher(dispatcher))

    async def invoke() -> None:
        with pytest.raises(RuntimeError, match="owner event loop is not running"):
            await dispatcher.run(lambda: asyncio.sleep(0))

    try:
        asyncio.run(invoke())
    finally:
        owner_loop.close()


async def _bind_dispatcher(dispatcher: Any) -> None:
    dispatcher.bind_current_loop()


def test_owner_loop_dispatcher_concurrent_first_calls_share_winning_loop():
    dispatcher = OwnerLoopDispatcher()
    calls_started = threading.Barrier(2)
    work_started = threading.Barrier(2)
    results: list[int] = []
    errors: list[BaseException] = []

    async def work() -> int:
        await asyncio.to_thread(work_started.wait)
        return id(asyncio.get_running_loop())

    def invoke() -> None:
        try:
            calls_started.wait()
            results.append(asyncio.run(dispatcher.run(work)))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not errors
    assert len(results) == 2
    assert results[0] == results[1]


def test_owner_loop_dispatcher_fails_if_loop_stops_during_dispatch():
    dispatcher = OwnerLoopDispatcher()
    owner_loop = asyncio.new_event_loop()
    owner_ready = threading.Event()
    stop_requested = threading.Event()
    let_stop_callback_return = threading.Event()
    first_run_stopped = threading.Event()
    cleanup_queued_callbacks = threading.Event()

    def owner_thread_main() -> None:
        asyncio.set_event_loop(owner_loop)

        def bind() -> None:
            dispatcher.bind_current_loop()
            owner_ready.set()

        owner_loop.call_soon(bind)
        owner_loop.run_forever()
        first_run_stopped.set()
        cleanup_queued_callbacks.wait()
        owner_loop.call_soon(owner_loop.stop)
        owner_loop.run_forever()
        owner_loop.close()

    owner_thread = threading.Thread(target=owner_thread_main)
    owner_thread.start()
    assert owner_ready.wait(timeout=1)

    def stop_inside_owner_callback() -> None:
        owner_loop.stop()
        stop_requested.set()
        let_stop_callback_return.wait()

    owner_loop.call_soon_threadsafe(stop_inside_owner_callback)
    assert stop_requested.wait(timeout=1)

    async def invoke() -> None:
        async def release_callback() -> None:
            await asyncio.sleep(0.05)
            let_stop_callback_return.set()

        release_task = asyncio.create_task(release_callback())
        with pytest.raises(RuntimeError, match="owner event loop stopped"):
            await asyncio.wait_for(
                dispatcher.run(lambda: asyncio.sleep(0)),
                timeout=1,
            )
        await release_task

    asyncio.run(invoke())
    assert first_run_stopped.wait(timeout=1)
    cleanup_queued_callbacks.set()
    owner_thread.join(timeout=2)
    assert not owner_thread.is_alive()


def test_owner_loop_dispatcher_prefers_completed_result_over_simultaneous_stop():
    dispatcher = OwnerLoopDispatcher()
    owner_loop = asyncio.new_event_loop()
    owner_ready = threading.Event()

    def owner_thread_main() -> None:
        asyncio.set_event_loop(owner_loop)

        def bind() -> None:
            dispatcher.bind_current_loop()
            owner_ready.set()

        owner_loop.call_soon(bind)
        owner_loop.run_forever()
        owner_loop.close()

    owner_thread = threading.Thread(target=owner_thread_main)
    owner_thread.start()
    assert owner_ready.wait(timeout=1)

    async def finish_and_stop() -> str:
        owner_loop.stop()
        return "committed"

    result = asyncio.run(dispatcher.run(finish_and_stop))

    owner_thread.join(timeout=2)
    assert result == "committed"
    assert not owner_thread.is_alive()


@pytest.mark.asyncio
async def test_async_concurrency_limiter_bounds_concurrency():
    limiter = AsyncConcurrencyLimiter(max_concurrent=3)
    release = asyncio.Event()
    entered = 0
    max_entered = 0
    limit_reached = asyncio.Event()

    async def operation() -> None:
        nonlocal entered, max_entered
        entered += 1
        max_entered = max(max_entered, entered)
        if entered == 3:
            limit_reached.set()
        await release.wait()
        entered -= 1

    tasks = [asyncio.create_task(limiter.run("test", operation)) for _ in range(20)]
    await asyncio.wait_for(limit_reached.wait(), timeout=1)
    await asyncio.sleep(0)

    assert max_entered == 3
    release.set()
    await asyncio.gather(*tasks)
    assert limiter.inflight == 0
    assert limiter.max_observed_inflight == 3


def test_async_concurrency_limiter_rejects_non_positive_limit():
    with pytest.raises(ValueError, match="max_concurrent must be positive"):
        AsyncConcurrencyLimiter(max_concurrent=0)


@pytest.mark.asyncio
async def test_run_to_completion_preserves_caller_cancellation_when_work_fails_later():
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail_later() -> None:
        started.set()
        await release.wait()
        raise RuntimeError("late store failure")

    operation = asyncio.create_task(run_to_completion(fail_later))
    await started.wait()
    operation.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await operation


@pytest.mark.asyncio
async def test_keyed_lock_serializes_same_key_and_allows_different_keys():
    pool = KeyedAsyncLockPool[str]()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    same_key_entered = asyncio.Event()
    other_key_entered = asyncio.Event()

    async def hold_first_key() -> None:
        async with pool.acquire("task-1"):
            first_entered.set()
            await release_first.wait()

    async def wait_for_same_key() -> None:
        await first_entered.wait()
        async with pool.acquire("task-1"):
            same_key_entered.set()

    async def use_other_key() -> None:
        await first_entered.wait()
        async with pool.acquire("task-2"):
            other_key_entered.set()

    tasks = [
        asyncio.create_task(hold_first_key()),
        asyncio.create_task(wait_for_same_key()),
        asyncio.create_task(use_other_key()),
    ]

    await asyncio.wait_for(other_key_entered.wait(), timeout=1)
    assert not same_key_entered.is_set()

    release_first.set()
    await asyncio.wait_for(same_key_entered.wait(), timeout=1)
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_keyed_lock_registry_is_cleaned_after_use_and_cancellation():
    pool = KeyedAsyncLockPool[str]()

    async with pool.acquire("completed"):
        assert pool.entry_count == 1
    assert pool.entry_count == 0

    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()

    async def blocker() -> None:
        async with pool.acquire("cancelled"):
            blocker_entered.set()
            await release_blocker.wait()

    async def waiter() -> None:
        await blocker_entered.wait()
        async with pool.acquire("cancelled"):
            pass

    blocker_task = asyncio.create_task(blocker())
    waiter_task = asyncio.create_task(waiter())
    await blocker_entered.wait()
    await asyncio.sleep(0)

    waiter_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_task

    release_blocker.set()
    await blocker_task
    assert pool.entry_count == 0
