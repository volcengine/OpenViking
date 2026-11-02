# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import gc
import threading
import weakref

import pytest


@pytest.mark.asyncio
async def test_work_index_groups_and_cancels_active_tasks_by_owner():
    from openviking.service.task_work_index import TaskWorkIndex

    index = TaskWorkIndex()
    first = asyncio.create_task(asyncio.Event().wait())
    second = asyncio.create_task(asyncio.Event().wait())
    index.register_active(
        "shared-id", first, account_id="account-a", user_id="user"
    )
    index.register_active(
        "shared-id", second, account_id="account-b", user_id="user"
    )

    assert index.active_tasks_by_owner() == {
        ("account-a", "user"): {"shared-id"},
        ("account-b", "user"): {"shared-id"},
    }

    index.cancel_active(
        "shared-id", account_id="account-a", user_id="user"
    )
    await asyncio.gather(first, return_exceptions=True)
    assert first.cancelled()
    assert not second.done()

    second.cancel()
    await asyncio.gather(second, return_exceptions=True)


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

    result = await asyncio.to_thread(lambda: asyncio.run(dispatcher.run(work)))

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


def test_owner_loop_dispatcher_rejects_closed_or_stopped_owner_loop():
    from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher

    closed_dispatcher = OwnerLoopDispatcher()

    async def bind_closed() -> None:
        closed_dispatcher.bind_current_loop()

    asyncio.run(bind_closed())

    async def invoke_closed() -> None:
        with pytest.raises(RuntimeError, match="owner event loop is closed"):
            await closed_dispatcher.run(lambda: asyncio.sleep(0))

    asyncio.run(invoke_closed())

    owner_loop = asyncio.new_event_loop()
    stopped_dispatcher = OwnerLoopDispatcher(owner_loop)
    owner_loop.run_until_complete(asyncio.sleep(0))

    async def invoke_stopped() -> None:
        with pytest.raises(RuntimeError, match="owner event loop is not running"):
            await stopped_dispatcher.run(lambda: asyncio.sleep(0))

    try:
        asyncio.run(invoke_stopped())
    finally:
        owner_loop.close()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_run_to_completion_releases_request_and_preserves_cancellation(cancel):
    from openviking.service.task_tracker_concurrency import run_to_completion

    started = asyncio.Event()
    release = asyncio.Event()

    class RequestPayload:
        pass

    async def fail_later() -> None:
        started.set()
        await release.wait()
        raise RuntimeError("late store failure")

    async def request() -> weakref.ReferenceType[RequestPayload]:
        payload = RequestPayload()
        try:
            await run_to_completion(fail_later)
        except (asyncio.CancelledError, RuntimeError) as exc:
            assert type(exc) is (asyncio.CancelledError if cancel else RuntimeError)
            if not cancel:
                assert str(exc) == "late store failure"
        else:
            pytest.fail("The operation must propagate failure or caller cancellation")
        return weakref.ref(payload)

    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        operation = asyncio.create_task(request())
        await started.wait()
        if cancel:
            operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        release.set()
        payload_ref = await operation
        await asyncio.sleep(0)
        assert payload_ref() is None
    finally:
        release.set()
        if gc_was_enabled:
            gc.enable()


@pytest.mark.asyncio
async def test_keyed_lock_serializes_same_key_and_allows_different_keys():
    from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool

    pool = KeyedAsyncLockPool[str]()
    same_key_entered = threading.Event()
    other_key_entered = threading.Event()

    async def wait_for_same_key() -> None:
        async with pool.acquire("task-1"):
            same_key_entered.set()

    async def use_other_key() -> None:
        async with pool.acquire("task-2"):
            other_key_entered.set()

    async def work() -> None:
        await asyncio.gather(wait_for_same_key(), use_other_key())

    async with pool.acquire("task-1"):
        worker = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(work())))
        assert await asyncio.to_thread(other_key_entered.wait, 3)
        assert not same_key_entered.is_set()

    await asyncio.wait_for(worker, timeout=3)
    assert same_key_entered.is_set()


@pytest.mark.asyncio
async def test_keyed_lock_registry_is_cleaned_after_waiter_cancellation():
    from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool

    pool = KeyedAsyncLockPool[str]()
    blocker_entered = asyncio.Event()
    release_blocker = asyncio.Event()

    async def blocker() -> None:
        async with pool.acquire("cancelled"):
            blocker_entered.set()
            await release_blocker.wait()

    async def waiter() -> None:
        async with pool.acquire("cancelled"):
            pass

    blocker_task = asyncio.create_task(blocker())
    await blocker_entered.wait()
    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)

    waiter_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_task

    release_blocker.set()
    await blocker_task
    assert pool.entry_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "identity-query", "cancel"])
async def test_file_store_concurrent_fixed_id_create_is_idempotent(failure):
    from openviking.service.task_store import PersistentTaskStore
    from openviking.service.task_tracker import TaskStatus, TaskTracker
    from tests.test_task_tracker import _FakeAgfs

    store = PersistentTaskStore(_FakeAgfs())
    writer, replay = TaskTracker(store), TaskTracker(store)
    entered, release = asyncio.Event(), asyncio.Event()
    create = store.create

    async def delayed_create(task, **kwargs):
        if task.meta.get("delayed"):
            entered.set()
            await release.wait()
        return await create(task, **kwargs)

    store.create = delayed_create
    checks = 0

    async def guard(account_id, user_id):
        nonlocal checks
        checks += 1
        if checks > 1 and failure == "identity-query":
            raise ConnectionError("Identity storage unavailable")
        return False

    replay.set_deletion_guard(guard)
    owner = {"account_id": "acme", "user_id": "alice"}
    duplicate = asyncio.create_task(
        replay.create("add_resource", task_id="fixed", meta={"delayed": True}, **owner)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        first = await writer.create("add_resource", task_id="fixed", **owner)
        await writer.start(first.task_id, **owner)
        await writer.register_work(first.task_id, "work", "Semantic", **owner)
        if failure == "cancel":
            duplicate.cancel()
    finally:
        release.set()
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await duplicate
    else:
        second = await duplicate
        assert first.task_id == second.task_id == "fixed"
    assert (await writer.get("fixed", **owner)).status is TaskStatus.RUNNING
    assert await writer.has_work("fixed", **owner)
    assert len(await writer.list_tasks(**owner)) == 1


@pytest.mark.asyncio
async def test_file_store_create_if_no_running_is_unique_per_business_key():
    from openviking.service.task_store import PersistentTaskStore
    from openviking.service.task_tracker import TaskTracker
    from tests.test_task_tracker import _FakeAgfs

    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
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

    assert sum(task is not None for task in results) == 1
