# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading
from copy import deepcopy
from typing import Any

import pytest

from openviking.service.task_domain import TaskAggregate
from openviking.service.task_store import CachingTaskWorkStore

OWNER = {"account_id": "acme", "user_id": "alice"}


class _ControllableTaskStore:
    def __init__(self) -> None:
        self.payloads: dict[str, dict[str, Any]] = {}
        self.aggregates: dict[str, TaskAggregate] = {}
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
        self.aggregates[task.task_id] = TaskAggregate(task=deepcopy(task))

    async def create_if_no_active(self, task: Any) -> bool:
        for aggregate in self.aggregates.values():
            current = aggregate.task
            if (
                current.account_id == task.account_id
                and current.user_id == task.user_id
                and current.task_type == task.task_type
                and current.resource_id == task.resource_id
                and current.status.value in {"pending", "running", "cancelling"}
            ):
                return False
        await self.create(task)
        return True

    async def update(self, task: Any) -> bool:
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
        existing = self.aggregates.get(task.task_id)
        self.aggregates[task.task_id] = TaskAggregate(
            task=deepcopy(task),
            works=deepcopy(existing.works) if existing is not None else {},
        )
        return True

    async def get(
        self,
        task_id: str,
        *,
        account_id: str | None = None,
        user_id: str | None = None,
    ) -> TaskAggregate | None:
        self.operation_loops.append(asyncio.get_running_loop())
        self.get_calls += 1
        aggregate = self.aggregates.get(task_id)
        if aggregate is None:
            return None
        if account_id is not None and aggregate.task.account_id != account_id:
            return None
        if user_id is not None and aggregate.task.user_id != user_id:
            return None
        return deepcopy(aggregate)

    async def list(
        self,
        account_id: str | None = None,
        *,
        user_id: str | None = None,
        task_type: str | None = None,
        status: str | None = None,
        resource_id: str | None = None,
        limit: int | None = None,
    ) -> list[TaskAggregate]:
        self.operation_loops.append(asyncio.get_running_loop())
        snapshot = [
            deepcopy(aggregate)
            for aggregate in self.aggregates.values()
            if (account_id is None or aggregate.task.account_id == account_id)
            and (user_id is None or aggregate.task.user_id == user_id)
            and (task_type is None or aggregate.task.task_type == task_type)
            and (status is None or aggregate.task.status.value == status)
            and (resource_id is None or aggregate.task.resource_id == resource_id)
        ]
        if self.list_started is not None:
            self.list_started.set()
        if self.list_release is not None:
            await self.list_release.wait()
        snapshot.sort(key=lambda aggregate: aggregate.task.created_at, reverse=True)
        return snapshot if limit is None else snapshot[:limit]

    async def delete(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str | None = None,
    ) -> None:
        self.payloads.pop(task_id, None)
        self.aggregates.pop(task_id, None)

    async def list_cancelling_tasks(self) -> set[tuple[str, str, str]]:
        return {
            (aggregate.task.account_id, aggregate.task.user_id, task_id)
            for task_id, aggregate in self.aggregates.items()
            if aggregate.task.status.value == "cancelling"
        }


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

    async def update(self, task: Any) -> bool:
        if not self._block_next_update:
            return await super().update(task)
        self._block_next_update = False
        payload = self._payload(task)

        def blocking_write() -> None:
            self.thread_update_started.set()
            self.thread_update_release.wait()
            self.payloads[task.task_id] = payload
            existing = self.aggregates.get(task.task_id)
            self.aggregates[task.task_id] = TaskAggregate(
                task=deepcopy(task),
                works=deepcopy(existing.works) if existing is not None else {},
            )

        self.operation_loops.append(asyncio.get_running_loop())
        await asyncio.to_thread(blocking_write)
        return True


def _tracker(store: _ControllableTaskStore, **kwargs) -> Any:
    from openviking.service.task_tracker import TaskTracker

    return TaskTracker(store=CachingTaskWorkStore(store), **kwargs)


@pytest.mark.asyncio
async def test_task_tracker_different_task_updates_do_not_block_each_other():
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    first = await tracker.create("add_resource", account_id="acme", user_id="alice")
    second = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.update_started[first.task_id] = asyncio.Event()
    store.update_release[first.task_id] = asyncio.Event()

    first_update = asyncio.create_task(tracker.start(first.task_id, **OWNER))
    await store.update_started[first.task_id].wait()

    await asyncio.wait_for(tracker.start(second.task_id, **OWNER), timeout=1)
    second_snapshot = await tracker.get(second.task_id, **OWNER)
    assert second_snapshot is not None
    assert second_snapshot.status == TaskStatus.RUNNING

    store.update_release[first.task_id].set()
    await first_update


@pytest.mark.asyncio
async def test_task_tracker_cache_hit_get_is_not_blocked_by_in_flight_update():
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.update_started[task.task_id] = asyncio.Event()
    store.update_release[task.task_id] = asyncio.Event()

    update = asyncio.create_task(tracker.start(task.task_id, **OWNER))
    await store.update_started[task.task_id].wait()

    snapshot = await asyncio.wait_for(tracker.get(task.task_id, **OWNER), timeout=0.1)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.PENDING

    store.update_release[task.task_id].set()
    await update


@pytest.mark.asyncio
async def test_task_tracker_failed_update_does_not_contaminate_cached_snapshot():
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.update_errors[task.task_id] = RuntimeError("store unavailable")

    with pytest.raises(RuntimeError, match="store unavailable"):
        await tracker.start(task.task_id, **OWNER)

    snapshot = await tracker.get(task.task_id, **OWNER)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_cancelled_thread_write_settles_before_later_same_task_mutation():
    from openviking.service.task_tracker import TaskStatus

    store = _ThreadBlockingUpdateTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")

    starting = asyncio.create_task(tracker.start(task.task_id, **OWNER))
    await asyncio.to_thread(store.thread_update_started.wait)
    starting.cancel()
    completing = asyncio.create_task(tracker.complete(task.task_id, {"done": True}, **OWNER))
    await asyncio.sleep(0)
    assert not completing.done()

    store.thread_update_release.set()
    with pytest.raises(asyncio.CancelledError):
        await starting
    await completing

    snapshot = await tracker.get(task.task_id, **OWNER)
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

    store = _ThreadBlockingUpdateTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")

    if outcome == "complete":
        operation = asyncio.create_task(tracker.complete(task.task_id, {"done": True}, **OWNER))
    else:
        operation = asyncio.create_task(tracker.fail(task.task_id, "failed on purpose", **OWNER))
    await asyncio.to_thread(store.thread_update_started.wait)
    operation.cancel()
    store.thread_update_release.set()

    with pytest.raises(asyncio.CancelledError):
        await operation

    snapshot = await tracker.get(task.task_id, **OWNER)
    assert snapshot is not None
    assert snapshot.status.value == expected_status
    assert store.payloads[task.task_id]["status"] == expected_status
    assert tracker._task_locks.entry_count == 0
    assert tracker._store_io.inflight == 0


@pytest.mark.asyncio
async def test_cancelled_cancel_request_still_cancels_active_work_and_finalizes():
    from openviking.service.task_tracker import TaskStatus

    store = _ThreadBlockingUpdateTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    work_started = asyncio.Event()
    work_cancelled = asyncio.Event()

    async def active_work() -> None:
        tracker.register_active(task.task_id, **OWNER)
        work_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            work_cancelled.set()
            raise
        finally:
            await tracker.unregister_active(task.task_id, **OWNER)

    worker = asyncio.create_task(active_work())
    await work_started.wait()
    cancelling = asyncio.create_task(tracker.cancel(task.task_id, **OWNER))
    await asyncio.to_thread(store.thread_update_started.wait)
    cancelling.cancel()
    store.thread_update_release.set()

    with pytest.raises(asyncio.CancelledError):
        await cancelling
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(worker, timeout=1)
    await asyncio.wait_for(work_cancelled.wait(), timeout=1)

    snapshot = await tracker.get(task.task_id, **OWNER)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.CANCELLED
    assert store.payloads[task.task_id]["status"] == TaskStatus.CANCELLED.value
    assert tracker._task_locks.entry_count == 0
    assert tracker._store_io.inflight == 0


@pytest.mark.asyncio
async def test_register_active_rechecks_cancellation_without_poll_loop():
    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", **OWNER)
    aggregate = store.aggregates[task.task_id]
    from openviking.service.task_domain import WorkRecord

    aggregate.works["work-1"] = WorkRecord(
        work_id="work-1", task_id=task.task_id, queue_name="Semantic"
    )
    tracker._store._replace_cached_for_test(aggregate)
    await tracker.cancel(task.task_id, **OWNER)
    cancelled = asyncio.Event()

    async def late_registration() -> None:
        tracker.register_active(task.task_id, **OWNER)
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        finally:
            await tracker.unregister_active(task.task_id, **OWNER)

    worker = asyncio.create_task(late_registration())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(worker, timeout=1)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_user_cancel_signal_is_claimed_once_per_active_handle():
    tracker = _tracker(_ControllableTaskStore())
    release = asyncio.Event()

    async def active_work() -> None:
        await release.wait()

    worker = asyncio.create_task(active_work())
    key = ("acme", "alice", "task-1")
    with tracker._active_lock:
        tracker._active[key] = {worker: False}

    first, second = await asyncio.gather(
        asyncio.to_thread(tracker._claim_user_cancel_handles, {key}),
        asyncio.to_thread(tracker._claim_user_cancel_handles, {key}),
    )

    assert sorted([len(first), len(second)]) == [0, 1]
    assert (first or second) == [worker]
    release.set()
    await worker


@pytest.mark.asyncio
async def test_cancel_request_cancelled_before_store_admission_does_not_cancel_work():
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store, max_concurrent_store_io=1)
    blocker = await tracker.create("add_resource", account_id="acme", user_id="alice")
    target = await tracker.create("add_resource", account_id="acme", user_id="alice")
    await tracker.start(target.task_id, **OWNER)
    store.update_started[blocker.task_id] = asyncio.Event()
    store.update_release[blocker.task_id] = asyncio.Event()

    blocking_update = asyncio.create_task(tracker.start(blocker.task_id, **OWNER))
    await store.update_started[blocker.task_id].wait()

    work_started = asyncio.Event()
    work_cancelled = asyncio.Event()

    async def active_work() -> None:
        tracker.register_active(target.task_id, **OWNER)
        work_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            work_cancelled.set()
            raise
        finally:
            await tracker.unregister_active(target.task_id, **OWNER)

    worker = asyncio.create_task(active_work())
    await work_started.wait()
    cancelling = asyncio.create_task(tracker.cancel(target.task_id, **OWNER))
    for _ in range(100):
        if tracker._task_locks.entry_count == 2:
            break
        await asyncio.sleep(0.01)

    cancelling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(cancelling, timeout=1)

    snapshot = await tracker.get(target.task_id, **OWNER)
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
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    await tracker.start(task.task_id, **OWNER)
    store.update_started[task.task_id] = asyncio.Event()
    store.update_release[task.task_id] = asyncio.Event()

    completing = asyncio.create_task(tracker.complete(task.task_id, {"done": True}, **OWNER))
    await store.update_started[task.task_id].wait()
    late_start = asyncio.create_task(tracker.start(task.task_id, stage="late-start", **OWNER))
    late_stage = asyncio.create_task(tracker.update_stage(task.task_id, "late-stage", **OWNER))

    store.update_release[task.task_id].set()
    await asyncio.gather(completing, late_start, late_stage)

    snapshot = await tracker.get(task.task_id, **OWNER)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.COMPLETED
    assert snapshot.stage == "completed"


@pytest.mark.asyncio
async def test_finalize_cas_conflict_reloads_new_work_before_terminalizing():
    from openviking.service.task_domain import WorkRecord
    from openviking.service.task_tracker import TaskStatus

    class WorkRacingStore(_ControllableTaskStore):
        def __init__(self):
            super().__init__()
            self.update_count = 0

        async def update(self, task):
            self.update_count += 1
            # start=1, outcome=2, first finalize attempt=3. Simulate another
            # node inserting work and advancing the aggregate revision there.
            if self.update_count == 3:
                aggregate = self.aggregates[task.task_id]
                aggregate.works["remote-work"] = WorkRecord(
                    work_id="remote-work",
                    task_id=task.task_id,
                    queue_name="Semantic",
                )
                return False
            return await super().update(task)

        async def mark_work_done(self, task_id, work_id, *, account_id, user_id):
            aggregate = self.aggregates[task_id]
            aggregate.mark_work_done(work_id)
            aggregate.task.version += 1

    from openviking.service.task_tracker import TaskTracker

    tracker = TaskTracker(WorkRacingStore())
    task = await tracker.create("add_resource", **OWNER)
    await tracker.start(task.task_id, **OWNER)
    await tracker.complete(task.task_id, {"ok": True}, **OWNER)

    blocked = await tracker.get(task.task_id, **OWNER)
    assert blocked is not None and blocked.status == TaskStatus.RUNNING
    assert await tracker.has_work(task.task_id, **OWNER)

    await tracker.settle_work(task.task_id, "remote-work", **OWNER)
    completed = await tracker.get(task.task_id, **OWNER)
    assert completed is not None and completed.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_concurrent_create_with_fixed_task_id_is_idempotent():

    store = _ControllableTaskStore()
    tracker = _tracker(store)

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


@pytest.mark.asyncio
async def test_same_task_id_is_isolated_by_owner_in_cache_and_locks():
    from openviking.service.task_tracker import TaskStatus, TaskTracker

    # The file layout and public APIs are owner-scoped, so identical opaque IDs
    # must coexist without one owner's cache or cancellation touching another.
    class OwnerStore:
        def __init__(self):
            self.tasks = {}

        @staticmethod
        def key(task):
            return task.account_id, task.user_id, task.task_id

        async def create(self, task):
            self.tasks[self.key(task)] = TaskAggregate(task=deepcopy(task))

        async def update(self, task):
            key = self.key(task)
            current = self.tasks.get(key)
            self.tasks[key] = TaskAggregate(
                task=deepcopy(task),
                works=deepcopy(current.works) if current else {},
            )
            return True

        async def get(self, task_id, *, account_id, user_id):
            return deepcopy(self.tasks.get((account_id, user_id, task_id)))

        async def list(
            self,
            account_id=None,
            *,
            user_id=None,
            task_type=None,
            status=None,
            resource_id=None,
            limit=None,
        ):
            tasks = [
                deepcopy(aggregate)
                for (account, user, _), aggregate in self.tasks.items()
                if (account_id is None or account == account_id)
                and (user_id is None or user == user_id)
                and (task_type is None or aggregate.task.task_type == task_type)
                and (status is None or aggregate.task.status.value == status)
                and (resource_id is None or aggregate.task.resource_id == resource_id)
            ]
            tasks.sort(key=lambda aggregate: aggregate.task.created_at, reverse=True)
            return tasks if limit is None else tasks[:limit]

        async def list_cancelling_tasks(self):
            return {
                key
                for key, aggregate in self.tasks.items()
                if aggregate.task.status == TaskStatus.CANCELLING
            }

        async def delete(self, task_id, *, account_id, user_id):
            self.tasks.pop((account_id, user_id, task_id), None)

    store = CachingTaskWorkStore(OwnerStore())
    tracker = TaskTracker(store)
    alice = {"account_id": "acme", "user_id": "alice"}
    bob = {"account_id": "acme", "user_id": "bob"}

    await tracker.create("add_resource", task_id="shared", **alice)
    await tracker.create("add_resource", task_id="shared", **bob)

    bob_ready = asyncio.Event()
    bob_release = asyncio.Event()

    async def hold_bob_active(task_id, ready, release):
        tracker.register_active(task_id, **bob)
        ready.set()
        try:
            await release.wait()
        finally:
            await tracker.unregister_active(task_id, **bob)

    bob_worker = asyncio.create_task(hold_bob_active("shared", bob_ready, bob_release))
    await bob_ready.wait()
    await tracker.start("shared", **alice)
    await tracker.complete("shared", {"owner": "alice"}, **alice)

    alice_task = await tracker.get("shared", **alice)
    bob_task = await tracker.get("shared", **bob)
    assert alice_task is not None and alice_task.status == TaskStatus.COMPLETED
    assert alice_task.result == {"owner": "alice"}
    assert bob_task is not None and bob_task.status == TaskStatus.PENDING
    assert bob_task.result is None
    bob_release.set()
    await bob_worker

    await tracker.create("add_resource", task_id="shared-cancel", **alice)
    await tracker.create("add_resource", task_id="shared-cancel", **bob)
    bob_cancel_ready = asyncio.Event()
    bob_cancel_release = asyncio.Event()
    bob_worker = asyncio.create_task(
        hold_bob_active("shared-cancel", bob_cancel_ready, bob_cancel_release)
    )
    await bob_cancel_ready.wait()

    cancelled = await tracker.cancel("shared-cancel", **alice)
    await asyncio.sleep(0)

    assert cancelled.status == TaskStatus.CANCELLED
    assert not bob_worker.done()
    bob_cancel_release.set()
    await bob_worker


@pytest.mark.asyncio
async def test_task_tracker_cache_hit_does_not_read_store():

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")

    snapshot = await tracker.get(task.task_id, account_id="acme", user_id="alice")

    assert snapshot is not None
    assert snapshot.task_id == task.task_id
    assert store.get_calls == 0


@pytest.mark.asyncio
async def test_task_tracker_list_does_not_replace_newer_cache_with_stale_store_data():
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    stale_payload = deepcopy(store.payloads[task.task_id])

    await tracker.start(task.task_id, **OWNER)
    store.payloads[task.task_id] = stale_payload

    snapshots = await tracker.list_tasks(account_id="acme", user_id="alice")

    assert len(snapshots) == 1
    assert snapshots[0].status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_blocked_store_list_does_not_block_cached_get():

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.list_started = asyncio.Event()
    store.list_release = asyncio.Event()

    listing = asyncio.create_task(tracker.list_tasks(account_id="acme", user_id="alice"))
    await store.list_started.wait()

    snapshot = await asyncio.wait_for(tracker.get(task.task_id, **OWNER), timeout=0.1)
    assert snapshot is not None
    assert snapshot.task_id == task.task_id

    store.list_release.set()
    await listing


@pytest.mark.asyncio
async def test_stale_in_flight_list_does_not_overwrite_completed_cache():
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    await tracker.start(task.task_id, **OWNER)
    store.list_started = asyncio.Event()
    store.list_release = asyncio.Event()

    listing = asyncio.create_task(tracker.list_tasks(account_id="acme", user_id="alice"))
    await store.list_started.wait()
    await tracker.complete(task.task_id, {"root_uri": "viking://resources/done"}, **OWNER)

    store.list_release.set()
    await listing

    snapshot = await tracker.get(task.task_id, **OWNER)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_clock_rollback_cannot_make_stale_store_snapshot_look_newer(monkeypatch):
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    await tracker.start(task.task_id, **OWNER)
    stale_payload = deepcopy(store.payloads[task.task_id])
    monkeypatch.setattr("openviking.service.task_tracker.time.time", lambda: 0.0)

    await tracker.complete(task.task_id, {"done": True}, **OWNER)
    store.payloads[task.task_id] = stale_payload
    await tracker.list_tasks(account_id="acme", user_id="alice")

    snapshot = await tracker.get(task.task_id, **OWNER)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.COMPLETED
    assert snapshot.updated_at > stale_payload["updated_at"]


@pytest.mark.asyncio
async def test_task_tracker_store_io_limit_allows_bounded_parallelism():

    store = _BlockingCreateTaskStore(expected_concurrency=3)
    tracker = _tracker(store, max_concurrent_store_io=3)

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

    store = _BlockingCreateTaskStore(expected_concurrency=1)
    tracker = _tracker(store, max_concurrent_store_io=1)

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

    store = _BlockingCreateTaskStore(expected_concurrency=1)
    tracker = _tracker(store)
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
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    owner_loop = asyncio.get_running_loop()
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")

    await asyncio.to_thread(lambda: asyncio.run(tracker.start(task.task_id, **OWNER)))

    tracker._store._invalidate(task.task_id, **OWNER)
    loaded = await asyncio.to_thread(
        lambda: asyncio.run(tracker.get(task.task_id, account_id="acme", user_id="alice"))
    )

    snapshot = await tracker.get(task.task_id, **OWNER)
    assert loaded is not None
    assert snapshot is not None
    assert snapshot.status == TaskStatus.RUNNING
    assert all(loop is owner_loop for loop in store.operation_loops)


@pytest.mark.asyncio
async def test_cancelling_foreign_mutation_cleans_task_lock_and_store_slot():
    from openviking.service.task_tracker import TaskStatus

    store = _ControllableTaskStore()
    tracker = _tracker(store)
    task = await tracker.create("add_resource", account_id="acme", user_id="alice")
    store.update_started[task.task_id] = asyncio.Event()
    store.update_release[task.task_id] = asyncio.Event()
    cancel_foreign = threading.Event()

    def run_from_foreign_loop() -> None:
        async def invoke() -> None:
            mutation = asyncio.create_task(tracker.start(task.task_id, **OWNER))
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
    snapshot = await tracker.get(task.task_id, **OWNER)
    assert snapshot is not None
    assert snapshot.status == TaskStatus.RUNNING
    assert tracker._task_locks.entry_count == 0
    assert tracker._store_io.inflight == 0
    assert store.payloads[task.task_id]["status"] == TaskStatus.RUNNING.value


@pytest.mark.asyncio
async def test_create_if_no_running_is_unique_per_business_key():

    store = _ControllableTaskStore()
    tracker = _tracker(store)

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

    store = _BlockingCreateTaskStore(expected_concurrency=2)
    tracker = _tracker(store)

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

    store = _ControllableTaskStore()
    tracker = _tracker(store)

    tasks = await asyncio.gather(
        *[tracker.create("add_resource", account_id="acme", user_id="alice") for _ in range(100)]
    )
    await asyncio.gather(*(tracker.start(task.task_id, **OWNER) for task in tasks))
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
