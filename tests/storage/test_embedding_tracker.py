# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import concurrent.futures
import threading
import time

import pytest

from openviking.storage.queuefs.embedding_tracker import EmbeddingTaskTracker


class _LoopThread:
    def __init__(self, close_delay: float = 0) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._close_delay = close_delay
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._ready.wait(timeout=2)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()
        if self._close_delay:
            time.sleep(self._close_delay)
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        if self.loop.is_closed():
            return
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)

    def stop_without_join(self) -> None:
        if self.loop.is_closed():
            return
        self.loop.call_soon_threadsafe(self.loop.stop)

    def join(self) -> None:
        self.thread.join(timeout=3)


@pytest.fixture(autouse=True)
def _reset_tracker_singleton():
    EmbeddingTaskTracker._instance = None
    EmbeddingTaskTracker._initialized = False
    yield
    EmbeddingTaskTracker._instance = None
    EmbeddingTaskTracker._initialized = False


def test_tracker_runs_completion_callback_on_register_loop():
    tracker = EmbeddingTaskTracker.get_instance()
    owner = _LoopThread()
    worker = _LoopThread()
    callback_info = concurrent.futures.Future()

    async def on_complete():
        callback_info.set_result((threading.get_ident(), asyncio.get_running_loop()))
        await asyncio.sleep(0)

    async def register():
        await tracker.register("semantic-msg", 1, on_complete=on_complete)

    async def decrement():
        return await tracker.decrement("semantic-msg")

    try:
        owner.submit(register()).result(timeout=2)
        assert not callback_info.done()

        remaining = worker.submit(decrement()).result(timeout=2)
        callback_thread_id, callback_loop = callback_info.result(timeout=2)
    finally:
        owner.stop()
        worker.stop()

    assert remaining == 0
    assert callback_thread_id == owner.thread.ident
    assert callback_loop is owner.loop


def test_tracker_falls_back_to_current_loop_when_owner_loop_is_closed():
    tracker = EmbeddingTaskTracker.get_instance()
    owner = _LoopThread()
    worker = _LoopThread()
    callback_info = concurrent.futures.Future()

    async def on_complete():
        callback_info.set_result((threading.get_ident(), asyncio.get_running_loop()))

    async def register():
        await tracker.register("semantic-msg", 1, on_complete=on_complete)

    async def decrement():
        return await tracker.decrement("semantic-msg")

    try:
        owner.submit(register()).result(timeout=2)
        owner.stop()

        remaining = worker.submit(decrement()).result(timeout=2)
        callback_thread_id, callback_loop = callback_info.result(timeout=2)
    finally:
        worker.stop()

    assert remaining == 0
    assert callback_thread_id == worker.thread.ident
    assert callback_loop is worker.loop


def test_tracker_falls_back_to_current_loop_when_owner_loop_is_stopped():
    tracker = EmbeddingTaskTracker.get_instance()
    owner = _LoopThread(close_delay=1)
    worker = _LoopThread()
    callback_info = concurrent.futures.Future()

    async def on_complete():
        callback_info.set_result((threading.get_ident(), asyncio.get_running_loop()))

    async def register():
        await tracker.register("semantic-msg", 1, on_complete=on_complete)

    async def decrement():
        return await tracker.decrement("semantic-msg")

    try:
        owner.submit(register()).result(timeout=2)
        owner.stop_without_join()
        time.sleep(0.1)

        remaining = worker.submit(decrement()).result(timeout=2)
        callback_thread_id, callback_loop = callback_info.result(timeout=2)
    finally:
        worker.stop()
        owner.join()

    assert remaining == 0
    assert callback_thread_id == worker.thread.ident
    assert callback_loop is worker.loop


@pytest.mark.asyncio
async def test_tracker_runs_zero_task_callback_immediately():
    tracker = EmbeddingTaskTracker.get_instance()
    callback_loop = None

    async def on_complete():
        nonlocal callback_loop
        callback_loop = asyncio.get_running_loop()

    await tracker.register("semantic-msg", 0, on_complete=on_complete)

    assert callback_loop is asyncio.get_running_loop()


@pytest.mark.asyncio
async def test_tracker_supports_sync_callback_and_missing_task():
    tracker = EmbeddingTaskTracker.get_instance()
    callback_calls = []

    await tracker.register("semantic-msg", 1, on_complete=lambda: callback_calls.append("done"))
    remaining = await tracker.decrement("semantic-msg")

    assert remaining == 0
    assert callback_calls == ["done"]
    assert await tracker.decrement("missing-semantic-msg") is None


@pytest.mark.asyncio
async def test_tracker_clears_zero_task_entry_without_callback():
    tracker = EmbeddingTaskTracker.get_instance()

    await tracker.register("semantic-msg", 0, on_complete=None)

    assert await tracker.decrement("semantic-msg") is None
