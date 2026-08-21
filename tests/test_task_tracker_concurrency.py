# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading
from copy import deepcopy
from typing import Any

import pytest


class _ControllableTaskStore:
    def __init__(self) -> None:
        self.payloads: dict[str, dict[str, Any]] = {}
        self.create_calls = 0
        self.update_started: dict[str, asyncio.Event] = {}
        self.update_release: dict[str, asyncio.Event] = {}
        self.update_errors: dict[str, Exception] = {}
        self.list_started: asyncio.Event | None = None
        self.list_release: asyncio.Event | None = None
        self.get_calls = 0
        self.operation_loops: list[asyncio.AbstractEventLoop] = []

    @staticmethod
    def _payload(task: Any) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "status": task.status.value,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "resource_id": task.resource_id,
            "account_id": task.account_id,
            "user_id": task.user_id,
            "meta": deepcopy(task.meta),
            "stage": task.stage,
            "result": deepcopy(task.result),
            "error": task.error,
        }

    async def create(self, task: Any) -> None:
        self.operation_loops.append(asyncio.get_running_loop())
        self.create_calls += 1
        self.payloads[task.task_id] = self._payload(task)

    async def update(self, task: Any) -> None:
        self.operation_loops.append(asyncio.get_running_loop())
        started = self.update_started.get(task.task_id)
        if started is not None:
            started.set()
        release = self.update_release.get(task.task_id)
        if release is not None:
            await release.wait()
        error = self.update_errors.get(task.task_id)
        if error is not None:
            raise error
        self.payloads[task.task_id] = self._payload(task)

    async def get(
        self,
        task_id: str,
        *,
        account_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        self.operation_loops.append(asyncio.get_running_loop())
        self.get_calls += 1
        payload = self.payloads.get(task_id)
        if payload is None:
            return None
        if account_id is not None and payload["account_id"] != account_id:
            return None
        if user_id is not None and payload["user_id"] != user_id:
            return None
        return deepcopy(payload)

    async def list(
        self,
        account_id: str,
        *,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.operation_loops.append(asyncio.get_running_loop())
        snapshot = [
            deepcopy(payload)
            for payload in self.payloads.values()
            if payload["account_id"] == account_id
            and (user_id is None or payload["user_id"] == user_id)
        ]
        if self.list_started is not None:
            self.list_started.set()
        if self.list_release is not None:
            await self.list_release.wait()
        return snapshot

    async def delete(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str | None = None,
    ) -> None:
        self.payloads.pop(task_id, None)


class _BlockingCreateTaskStore(_ControllableTaskStore):
    def __init__(self, expected_concurrency: int) -> None:
        super().__init__()
        self.expected_concurrency = expected_concurrency
        self.create_release = asyncio.Event()
        self.expected_entered = asyncio.Event()
        self.create_inflight = 0
        self.max_create_inflight = 0

    async def create(self, task: Any) -> None:
        self.create_inflight += 1
        self.max_create_inflight = max(self.max_create_inflight, self.create_inflight)
        if self.create_inflight == self.expected_concurrency:
            self.expected_entered.set()
        try:
            await self.create_release.wait()
            await super().create(task)
        finally:
            self.create_inflight -= 1


class _ThreadBlockingUpdateTaskStore(_ControllableTaskStore):
    def __init__(self) -> None:
        super().__init__()
        self.thread_update_started = threading.Event()
        self.thread_update_release = threading.Event()
        self._block_next_update = True

    async def update(self, task: Any) -> None:
        if not self._block_next_update:
            await super().update(task)
            return
        self._block_next_update = False
        payload = self._payload(task)

        def blocking_write() -> None:
            self.thread_update_started.set()
            self.thread_update_release.wait()
            self.payloads[task.task_id] = payload

        self.operation_loops.append(asyncio.get_running_loop())
        await asyncio.to_thread(blocking_write)


@pytest.mark.asyncio
async def test_owner_loop_dispatcher_runs_foreign_loop_work_on_owner_loop():
    from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher

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
    from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher

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
    from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher

    dispatcher = OwnerLoopDispatcher()

    async def bind() -> None:
        dispatcher.bind_current_loop()

    asyncio.run(bind())

    async def invoke() -> None:
        with pytest.raises(RuntimeError, match="owner event loop is closed"):
            await dispatcher.run(lambda: asyncio.sleep(0))

    asyncio.run(invoke())


def test_owner_loop_dispatcher_rejects_stopped_owner_loop():
    from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher

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
    from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher

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
    from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher

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
    from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher

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
async def test_store_io_limiter_bounds_concurrency():
    from openviking.service.task_tracker_concurrency import StoreIOLimiter

    limiter = StoreIOLimiter(max_concurrent=3)
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


def test_store_io_limiter_rejects_non_positive_limit():
    from openviking.service.task_tracker_concurrency import StoreIOLimiter

    with pytest.raises(ValueError, match="max_concurrent must be positive"):
        StoreIOLimiter(max_concurrent=0)


@pytest.mark.asyncio
async def test_run_to_completion_preserves_caller_cancellation_when_work_fails_later():
    from openviking.service.task_tracker_concurrency import run_to_completion

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
    from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool

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
    from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool

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


@pytest.mark.asyncio
async def test_task_tracker_different_task_updates_do_not_block_each_other():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    first = await tracker.create("add_resource", account_id="acme", user_id="alice")
    second = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.update_started[first.task_id] = asyncio.Event()
    store.update_release[first.task_id] = asyncio.Event()

    first_update = asyncio.create_task(tracker.start(first.task_id))
    await store.update_started[first.task_id].wait()

    await asyncio.wait_for(tracker.start(second.task_id), timeout=1)
    second_snapshot = await tracker.get(second.task_id)
    assert second_snapshot is not None
    assert second_snapshot.status == TaskStatus.RUNNING

    store.update_release[first.task_id].set()
    await first_update


@pytest.mark.asyncio
async def test_task_tracker_cache_hit_get_is_not_blocked_by_in_flight_update():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.update_started[task.task_id] = asyncio.Event()
    store.update_release[task.task_id] = asyncio.Event()

    update = asyncio.create_task(tracker.start(task.task_id))
    await store.update_started[task.task_id].wait()

    snapshot = await asyncio.wait_for(tracker.get(task.task_id), timeout=0.1)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.PENDING

    store.update_release[task.task_id].set()
    await update


@pytest.mark.asyncio
async def test_task_tracker_failed_update_does_not_contaminate_cached_snapshot():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.update_errors[task.task_id] = RuntimeError("store unavailable")

    with pytest.raises(RuntimeError, match="store unavailable"):
        await tracker.start(task.task_id)

    snapshot = await tracker.get(task.task_id)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_cancelled_thread_write_settles_before_later_same_task_mutation():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ThreadBlockingUpdateTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")

    starting = asyncio.create_task(tracker.start(task.task_id))
    await asyncio.to_thread(store.thread_update_started.wait)
    starting.cancel()
    completing = asyncio.create_task(tracker.complete(task.task_id, {"done": True}))
    await asyncio.sleep(0)
    assert not completing.done()

    store.thread_update_release.set()
    with pytest.raises(asyncio.CancelledError):
        await starting
    await completing

    snapshot = await tracker.get(task.task_id)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.COMPLETED
    assert store.payloads[task.task_id]["status"] == TaskStatus.COMPLETED.value
    assert tracker._task_locks.entry_count == 0
    assert tracker._store_io.inflight == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [("complete", "completed"), ("fail", "failed")],
)
async def test_cancelled_outcome_finishes_terminal_transition(outcome, expected_status):
    from openviking.service.task_tracker import TaskTracker

    store = _ThreadBlockingUpdateTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")

    if outcome == "complete":
        operation = asyncio.create_task(tracker.complete(task.task_id, {"done": True}))
    else:
        operation = asyncio.create_task(tracker.fail(task.task_id, "failed on purpose"))
    await asyncio.to_thread(store.thread_update_started.wait)
    operation.cancel()
    store.thread_update_release.set()

    with pytest.raises(asyncio.CancelledError):
        await operation

    snapshot = await tracker.get(task.task_id)
    assert snapshot is not None
    assert snapshot.status.value == expected_status
    assert store.payloads[task.task_id]["status"] == expected_status
    assert tracker._task_locks.entry_count == 0
    assert tracker._store_io.inflight == 0


@pytest.mark.asyncio
async def test_cancelled_cancel_request_still_cancels_active_work_and_finalizes():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ThreadBlockingUpdateTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    work_started = asyncio.Event()
    work_cancelled = asyncio.Event()

    async def active_work() -> None:
        tracker.register_running_task(task.task_id)
        work_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            work_cancelled.set()
            raise
        finally:
            await tracker.unregister_running_task(task.task_id)

    worker = asyncio.create_task(active_work())
    await work_started.wait()
    cancelling = asyncio.create_task(tracker.cancel(task.task_id))
    await asyncio.to_thread(store.thread_update_started.wait)
    cancelling.cancel()
    store.thread_update_release.set()

    with pytest.raises(asyncio.CancelledError):
        await cancelling
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(worker, timeout=1)
    await asyncio.wait_for(work_cancelled.wait(), timeout=1)

    snapshot = await tracker.get(task.task_id)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.CANCELLED
    assert store.payloads[task.task_id]["status"] == TaskStatus.CANCELLED.value
    assert tracker._task_locks.entry_count == 0
    assert tracker._store_io.inflight == 0


@pytest.mark.asyncio
async def test_cancel_request_cancelled_before_store_admission_does_not_cancel_work():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store, max_concurrent_store_io=1)
    blocker = await tracker.create("add_resource", account_id="acme", user_id="alice")
    target = await tracker.create("add_resource", account_id="acme", user_id="alice")
    await tracker.start(target.task_id)
    store.update_started[blocker.task_id] = asyncio.Event()
    store.update_release[blocker.task_id] = asyncio.Event()

    blocking_update = asyncio.create_task(tracker.start(blocker.task_id))
    await store.update_started[blocker.task_id].wait()

    work_started = asyncio.Event()
    work_cancelled = asyncio.Event()

    async def active_work() -> None:
        tracker.register_running_task(target.task_id)
        work_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            work_cancelled.set()
            raise

    worker = asyncio.create_task(active_work())
    await work_started.wait()
    cancelling = asyncio.create_task(tracker.cancel(target.task_id))
    for _ in range(100):
        if tracker._task_locks.entry_count == 2:
            break
        await asyncio.sleep(0.01)

    cancelling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(cancelling, timeout=1)

    snapshot = await tracker.get(target.task_id)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.RUNNING
    assert not work_cancelled.is_set()
    assert not worker.done()

    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    store.update_release[blocker.task_id].set()
    await blocking_update


@pytest.mark.asyncio
async def test_same_task_late_mutations_cannot_revert_terminal_status():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    await tracker.start(task.task_id)
    store.update_started[task.task_id] = asyncio.Event()
    store.update_release[task.task_id] = asyncio.Event()

    completing = asyncio.create_task(tracker.complete(task.task_id, {"done": True}))
    await store.update_started[task.task_id].wait()
    late_start = asyncio.create_task(tracker.start(task.task_id, stage="late-start"))
    late_stage = asyncio.create_task(tracker.update_stage(task.task_id, "late-stage"))

    store.update_release[task.task_id].set()
    await asyncio.gather(completing, late_start, late_stage)

    snapshot = await tracker.get(task.task_id)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.COMPLETED
    assert snapshot.stage == "completed"


@pytest.mark.asyncio
async def test_concurrent_create_with_fixed_task_id_is_idempotent():
    from openviking.service.task_tracker import TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)

    first, second = await asyncio.gather(
        tracker.create(
            "add_resource",
            task_id="fixed",
            account_id="acme",
            user_id="alice",
        ),
        tracker.create(
            "add_resource",
            task_id="fixed",
            account_id="acme",
            user_id="alice",
        ),
    )

    assert first.task_id == second.task_id == "fixed"
    assert store.create_calls == 1
    with pytest.raises(ValueError, match="already belongs to another owner"):
        await tracker.create(
            "add_resource",
            task_id="fixed",
            account_id="acme",
            user_id="bob",
        )


@pytest.mark.asyncio
async def test_task_tracker_cache_hit_does_not_read_store():
    from openviking.service.task_tracker import TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")

    snapshot = await tracker.get(task.task_id, account_id="acme", user_id="alice")

    assert snapshot is not None
    assert snapshot.task_id == task.task_id
    assert store.get_calls == 0


@pytest.mark.asyncio
async def test_task_tracker_list_does_not_replace_newer_cache_with_stale_store_data():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    stale_payload = deepcopy(store.payloads[task.task_id])

    await tracker.start(task.task_id)
    store.payloads[task.task_id] = stale_payload

    snapshots = await tracker.list_tasks(account_id="acme", user_id="alice")

    assert len(snapshots) == 1
    assert snapshots[0].status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_blocked_store_list_does_not_block_cached_get():
    from openviking.service.task_tracker import TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.list_started = asyncio.Event()
    store.list_release = asyncio.Event()

    listing = asyncio.create_task(tracker.list_tasks(account_id="acme", user_id="alice"))
    await store.list_started.wait()

    snapshot = await asyncio.wait_for(tracker.get(task.task_id), timeout=0.1)
    assert snapshot is not None
    assert snapshot.task_id == task.task_id

    store.list_release.set()
    await listing


@pytest.mark.asyncio
async def test_stale_in_flight_list_does_not_overwrite_completed_cache():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    await tracker.start(task.task_id)
    store.list_started = asyncio.Event()
    store.list_release = asyncio.Event()

    listing = asyncio.create_task(tracker.list_tasks(account_id="acme", user_id="alice"))
    await store.list_started.wait()
    await tracker.complete(task.task_id, {"root_uri": "viking://resources/done"})

    store.list_release.set()
    await listing

    snapshot = await tracker.get(task.task_id)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_clock_rollback_cannot_make_stale_store_snapshot_look_newer(monkeypatch):
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    await tracker.start(task.task_id)
    stale_payload = deepcopy(store.payloads[task.task_id])
    monkeypatch.setattr("openviking.service.task_tracker.time.time", lambda: 0.0)

    await tracker.complete(task.task_id, {"done": True})
    store.payloads[task.task_id] = stale_payload
    await tracker.list_tasks(account_id="acme", user_id="alice")

    snapshot = await tracker.get(task.task_id)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.COMPLETED
    assert snapshot.updated_at > stale_payload["updated_at"]


@pytest.mark.asyncio
async def test_task_tracker_store_io_limit_allows_bounded_parallelism():
    from openviking.service.task_tracker import TaskTracker

    store = _BlockingCreateTaskStore(expected_concurrency=3)
    tracker = TaskTracker(store=store, max_concurrent_store_io=3)

    creates = [
        asyncio.create_task(tracker.create("add_resource", account_id="acme", user_id="alice"))
        for _ in range(10)
    ]
    await asyncio.wait_for(store.expected_entered.wait(), timeout=1)
    await asyncio.sleep(0)

    assert store.max_create_inflight == 3
    store.create_release.set()
    await asyncio.gather(*creates)
    assert tracker._store_io.max_observed_inflight == 3
    assert tracker._store_io.inflight == 0


@pytest.mark.asyncio
async def test_cancelled_create_waiting_for_store_slot_has_no_side_effect():
    from openviking.service.task_tracker import TaskTracker

    store = _BlockingCreateTaskStore(expected_concurrency=1)
    tracker = TaskTracker(store=store, max_concurrent_store_io=1)

    first = asyncio.create_task(tracker.create("add_resource", account_id="acme", user_id="alice"))
    await asyncio.wait_for(store.expected_entered.wait(), timeout=1)

    waiting = asyncio.create_task(
        tracker.create("add_resource", account_id="acme", user_id="alice")
    )
    for _ in range(100):
        if tracker._task_locks.entry_count == 2:
            break
        await asyncio.sleep(0.01)
    assert tracker._task_locks.entry_count == 2

    waiting.cancel()
    await asyncio.sleep(0.05)
    cancelled_before_release = waiting.done()
    store.create_release.set()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    created = await first

    assert cancelled_before_release
    assert store.create_calls == 1
    assert set(store.payloads) == {created.task_id}
    assert tracker._task_locks.entry_count == 0
    assert tracker._store_io.inflight == 0


@pytest.mark.asyncio
async def test_cancelled_create_waiting_for_task_lock_finishes_before_lock_release():
    from openviking.service.task_tracker import TaskTracker

    store = _BlockingCreateTaskStore(expected_concurrency=1)
    tracker = TaskTracker(store=store)
    create_args = {
        "task_type": "add_resource",
        "task_id": "fixed",
        "account_id": "acme",
        "user_id": "alice",
    }

    first = asyncio.create_task(tracker.create(**create_args))
    await asyncio.wait_for(store.expected_entered.wait(), timeout=1)
    waiting = asyncio.create_task(tracker.create(**create_args))
    await asyncio.sleep(0)

    waiting.cancel()
    await asyncio.sleep(0.05)
    cancelled_before_release = waiting.done()
    store.create_release.set()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    await first
    assert cancelled_before_release
    assert store.create_calls == 1


@pytest.mark.asyncio
async def test_task_tracker_public_mutation_from_foreign_loop_runs_on_owner():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    owner_loop = asyncio.get_running_loop()
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")

    await asyncio.to_thread(lambda: asyncio.run(tracker.start(task.task_id)))

    with tracker._lock:
        tracker._tasks.pop(task.task_id)
    loaded = await asyncio.to_thread(
        lambda: asyncio.run(tracker.get(task.task_id, account_id="acme", user_id="alice"))
    )

    snapshot = await tracker.get(task.task_id)
    assert loaded is not None
    assert snapshot is not None
    assert snapshot.status == TaskStatus.RUNNING
    assert all(loop is owner_loop for loop in store.operation_loops)


@pytest.mark.asyncio
async def test_cancelling_foreign_mutation_cleans_task_lock_and_store_slot():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.update_started[task.task_id] = asyncio.Event()
    store.update_release[task.task_id] = asyncio.Event()
    cancel_foreign = threading.Event()

    def run_from_foreign_loop() -> None:
        async def invoke() -> None:
            mutation = asyncio.create_task(tracker.start(task.task_id))
            await asyncio.to_thread(cancel_foreign.wait)
            mutation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await mutation

        asyncio.run(invoke())

    foreign_call = asyncio.create_task(asyncio.to_thread(run_from_foreign_loop))
    await store.update_started[task.task_id].wait()
    cancel_foreign.set()
    await asyncio.sleep(0.05)
    returned_before_store_settled = foreign_call.done()
    store.update_release[task.task_id].set()
    await foreign_call

    assert not returned_before_store_settled
    snapshot = await tracker.get(task.task_id)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.RUNNING
    assert tracker._task_locks.entry_count == 0
    assert tracker._store_io.inflight == 0
    assert store.payloads[task.task_id]["status"] == TaskStatus.RUNNING.value


@pytest.mark.asyncio
async def test_create_if_no_running_is_unique_per_business_key():
    from openviking.service.task_tracker import TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)

    results = await asyncio.gather(
        *[
            tracker.create_if_no_running(
                "add_resource",
                "viking://resources/shared",
                account_id="acme",
                user_id="alice",
            )
            for _ in range(20)
        ]
    )

    created = [task for task in results if task is not None]
    assert len(created) == 1
    assert tracker._business_locks.entry_count == 0
    assert tracker._task_locks.entry_count == 0


@pytest.mark.asyncio
async def test_create_if_no_running_uses_independent_business_keys():
    from openviking.service.task_tracker import TaskTracker

    store = _BlockingCreateTaskStore(expected_concurrency=2)
    tracker = TaskTracker(store=store)

    first_call = asyncio.create_task(
        tracker.create_if_no_running(
            "add_resource",
            "viking://resources/one",
            account_id="acme",
            user_id="alice",
        )
    )
    second_call = asyncio.create_task(
        tracker.create_if_no_running(
            "add_resource",
            "viking://resources/two",
            account_id="acme",
            user_id="alice",
        )
    )
    await asyncio.wait_for(store.expected_entered.wait(), timeout=1)
    assert store.max_create_inflight == 2
    store.create_release.set()
    first, second = await asyncio.gather(first_call, second_call)

    assert first is not None
    assert second is not None
    assert first.task_id != second.task_id


@pytest.mark.asyncio
async def test_task_and_business_lock_registries_do_not_grow_with_completed_calls():
    from openviking.service.task_tracker import TaskTracker

    store = _ControllableTaskStore()
    tracker = TaskTracker(store=store)

    tasks = await asyncio.gather(
        *[tracker.create("add_resource", account_id="acme", user_id="alice") for _ in range(100)]
    )
    await asyncio.gather(*(tracker.start(task.task_id) for task in tasks))
    await asyncio.gather(
        *[
            tracker.create_if_no_running(
                "reindex",
                f"viking://resources/{index}",
                account_id="acme",
                user_id="alice",
            )
            for index in range(20)
        ]
    )

    assert tracker._task_locks.entry_count == 0
    assert tracker._business_locks.entry_count == 0
