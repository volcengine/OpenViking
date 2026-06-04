# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import concurrent.futures
import threading
import time

import pytest

from openviking.service.coordinator import (
    InProcessCoordinator,
    get_coordinator,
    set_coordinator,
)
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
    # The remaining-task counter now lives in the Coordinator; isolate each
    # test on a fresh in-process store (the default, non-distributed backend).
    set_coordinator(InProcessCoordinator())
    yield
    EmbeddingTaskTracker._instance = None
    EmbeddingTaskTracker._initialized = False
    set_coordinator(InProcessCoordinator())


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


# --- Distributed backend: cross-instance completion -------------------------
#
# Under the distributed (multi-instance) backend the remaining-task counter is
# shared, so embedding messages for one SemanticMsg can be decremented by an
# instance that holds neither the completion callback nor the loop it must run
# on. The owner instance watches the shared counter and fires the callback
# locally when it drains. A single shared coordinator store models two
# instances pointing at the same backend.


class _SharedDistributedCoordinator(InProcessCoordinator):
    """In-process store that advertises itself as cross-instance shared.

    Behaviourally identical to ``InProcessCoordinator`` (so the test needs no
    Redis), but ``is_distributed`` flips the tracker onto its owner-polls-for-
    completion path, exercising the exact multi-instance code under test.
    """

    is_distributed = True


class _FailOnceDistributedCoordinator(_SharedDistributedCoordinator):
    def __init__(
        self,
        *,
        fail_get_int_once: bool = False,
        fail_cleanup_delete_once: bool = False,
    ) -> None:
        super().__init__()
        self._fail_get_int_once = fail_get_int_once
        self._fail_cleanup_delete_once = fail_cleanup_delete_once
        self._fail_lock = threading.Lock()

    def get_int(self, key: str) -> int:
        if self._fail_get_int_once and key.endswith(":remaining"):
            with self._fail_lock:
                if self._fail_get_int_once:
                    self._fail_get_int_once = False
                    raise RuntimeError("transient get_int failure")
        return super().get_int(key)

    def delete(self, *keys: str) -> None:
        if self._fail_cleanup_delete_once and any(key.endswith(":reg") for key in keys):
            with self._fail_lock:
                if self._fail_cleanup_delete_once:
                    self._fail_cleanup_delete_once = False
                    raise RuntimeError("transient cleanup delete failure")
        return super().delete(*keys)


class TestDistributedCompletion:
    def test_owner_fires_callback_on_remote_decrement(self):
        # A shared store stands in for two instances on one backend. The owner
        # registers on loop A; a different loop drives the decrements; the
        # owner's poller must run the callback on loop A exactly when the
        # shared counter reaches zero.
        set_coordinator(_SharedDistributedCoordinator())
        tracker = EmbeddingTaskTracker.get_instance()
        owner = _LoopThread()
        worker = _LoopThread()
        callback_info = concurrent.futures.Future()

        async def on_complete():
            callback_info.set_result((threading.get_ident(), asyncio.get_running_loop()))

        async def register():
            await tracker.register("semantic-msg", 2, on_complete=on_complete)

        async def decrement():
            return await tracker.decrement("semantic-msg")

        try:
            owner.submit(register()).result(timeout=2)
            assert not callback_info.done()
            assert worker.submit(decrement()).result(timeout=2) == 1
            assert not callback_info.done()
            assert worker.submit(decrement()).result(timeout=2) == 0
            callback_thread_id, callback_loop = callback_info.result(timeout=3)
        finally:
            owner.stop()
            worker.stop()

        assert callback_thread_id == owner.thread.ident
        assert callback_loop is owner.loop
        coord = get_coordinator()
        assert coord.scard("emb:semantic-msg:reg") == 0
        assert coord.get_int("emb:semantic-msg:remaining") == 0

    def test_owner_fires_timeout_callback_on_distributed_stall(self, monkeypatch):
        monkeypatch.setattr(
            "openviking.storage.queuefs.embedding_tracker._POLL_INTERVAL_SEC",
            0.01,
        )
        set_coordinator(_SharedDistributedCoordinator())
        tracker = EmbeddingTaskTracker.get_instance()
        owner = _LoopThread()
        callback_info = concurrent.futures.Future()

        async def on_timeout(reason):
            callback_info.set_result((threading.get_ident(), asyncio.get_running_loop(), reason))

        async def register():
            await tracker.register(
                "semantic-msg",
                1,
                on_timeout=on_timeout,
                timeout_sec=0.05,
            )

        try:
            owner.submit(register()).result(timeout=2)
            callback_thread_id, callback_loop, reason = callback_info.result(timeout=2)
        finally:
            owner.stop()

        assert callback_thread_id == owner.thread.ident
        assert callback_loop is owner.loop
        assert "embedding completion timeout" in reason
        coord = get_coordinator()
        assert coord.scard("emb:semantic-msg:reg") == 0
        assert coord.get_int("emb:semantic-msg:remaining") == 0

    def test_owner_retries_after_transient_coordinator_read_error(self, monkeypatch):
        monkeypatch.setattr(
            "openviking.storage.queuefs.embedding_tracker._POLL_INTERVAL_SEC",
            0.01,
        )
        set_coordinator(_FailOnceDistributedCoordinator(fail_get_int_once=True))
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
            assert worker.submit(decrement()).result(timeout=2) == 0
            callback_thread_id, callback_loop = callback_info.result(timeout=3)
        finally:
            owner.stop()
            worker.stop()

        assert callback_thread_id == owner.thread.ident
        assert callback_loop is owner.loop

    def test_owner_retries_after_transient_cleanup_delete_error(self, monkeypatch):
        monkeypatch.setattr(
            "openviking.storage.queuefs.embedding_tracker._POLL_INTERVAL_SEC",
            0.01,
        )
        set_coordinator(_FailOnceDistributedCoordinator(fail_cleanup_delete_once=True))
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
            assert worker.submit(decrement()).result(timeout=2) == 0
            callback_thread_id, callback_loop = callback_info.result(timeout=3)
        finally:
            owner.stop()
            worker.stop()

        assert callback_thread_id == owner.thread.ident
        assert callback_loop is owner.loop
        coord = get_coordinator()
        assert coord.scard("emb:semantic-msg:reg") == 0
        assert coord.get_int("emb:semantic-msg:remaining") == 0

    def test_extra_decrements_do_not_double_fire(self):
        set_coordinator(_SharedDistributedCoordinator())
        tracker = EmbeddingTaskTracker.get_instance()
        owner = _LoopThread()
        worker = _LoopThread()
        fires = []
        fired = concurrent.futures.Future()

        async def on_complete():
            fires.append(1)
            fired.set_result(True)

        async def register():
            await tracker.register("semantic-msg", 1, on_complete=on_complete)

        async def decrement():
            return await tracker.decrement("semantic-msg")

        try:
            owner.submit(register()).result(timeout=2)
            assert worker.submit(decrement()).result(timeout=2) == 0
            fired.result(timeout=3)
            # Registration is cleared on completion, so later decrements are
            # no-ops and the poller has already exited.
            assert worker.submit(decrement()).result(timeout=2) is None
            time.sleep(0.7)  # span another poll interval to catch a double fire
        finally:
            owner.stop()
            worker.stop()

        assert fires == [1]


@pytest.mark.asyncio
async def test_tracker_does_not_auto_timeout_in_process(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker._POLL_INTERVAL_SEC",
        0.01,
    )
    tracker = EmbeddingTaskTracker.get_instance()
    timeout_calls = []

    await tracker.register(
        "semantic-msg",
        1,
        on_timeout=lambda reason: timeout_calls.append(reason),
        timeout_sec=0.05,
    )
    await asyncio.sleep(0.12)

    assert timeout_calls == []
    coord = get_coordinator()
    assert coord.scard("emb:semantic-msg:reg") == 1
    assert coord.get_int("emb:semantic-msg:remaining") == 1
    assert await tracker.decrement("semantic-msg") == 0
