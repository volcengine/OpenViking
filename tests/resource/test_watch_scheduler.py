# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime dispatch, recovery and cancellation contracts with persistent Tasks."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openviking.resource.feishu_watch_auth import FeishuOAuthClient, FeishuTokenRefreshError
from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_runtime import WatchRuntime
from openviking.resource.watch_store_file import FileWatchStore
from openviking.resource.watch_types import WatchPatch
from openviking.utils.git_auth import GIT_AUTH_FAILED
from openviking_cli.utils.config.parser_config import FeishuConfig
from tests.utils.watch_fixtures import shape_watch

TARGET = "viking://resources/doc"
OWNER = {"account_id": "default", "user_id": "default"}


class CountingFileWatchStore(FileWatchStore):
    running_reads = 0
    due_reads = 0

    async def running(self, *, after="", limit=100):
        self.running_reads += 1
        return await super().running(after=after, limit=limit)

    async def due_candidates(self, limit, *, offset=0):
        self.due_reads += 1
        return await super().due_candidates(limit, offset=offset)


@pytest.fixture
def service(watch_tracker):
    async def refresh(**kwargs):
        record = await watch_tracker.create("add_resource", resource_id=kwargs["to"], **OWNER)
        await kwargs["on_task_created"](record)
        await watch_tracker.complete(record.task_id, {}, **OWNER)
        return {"task_id": record.task_id}

    return SimpleNamespace(refresh_resource=AsyncMock(side_effect=refresh), _connector=None)


@pytest_asyncio.fixture
async def make_runtime(watch_vfs, watch_tracker, service):
    created = []

    async def factory(*, store_type=FileWatchStore, **kwargs):
        manager = WatchManager(
            store_type(watch_vfs),
            viking_fs=watch_vfs,
            tracker=watch_tracker,
            resource_service=service,
        )
        await manager.initialize()
        runtime = WatchRuntime(manager, **{"leader_enabled": False, **kwargs})
        await runtime.start()
        created.append(runtime)
        return runtime

    yield factory
    for runtime in created:
        await runtime.stop()
        await runtime.watch_manager.close()


async def create_watch(runtime, uri=TARGET, *, due=True, **values):
    manager = runtime.watch_manager
    await manager.locks._fs.mkdir(uri, exist_ok=True)
    values.setdefault("path", "https://example.org/doc")
    task = await manager.create_task(to_uri=uri, watch_interval=5, **values)
    if due:
        await shape_watch(manager.store, task.task_id, next_run_at=1.0)
    return task


async def drain(runtime, *, sweep=True):
    await asyncio.gather(*list(runtime._submissions.values()))
    if sweep:
        await runtime.sweep()


async def running_watch(runtime, tracker, *, missing=False, task_type="add_resource"):
    record = None if missing else await tracker.create(task_type, resource_id=TARGET, **OWNER)
    task = await create_watch(
        runtime, due=False, initial_run_id=record.task_id if record else "lost"
    )
    return task, record


@pytest.mark.asyncio
async def test_leader_loop_dispatches_due_watches(make_runtime, service):
    submitted = asyncio.Event()
    original = service.refresh_resource.side_effect

    async def refresh(**kwargs):
        result = await original(**kwargs)
        submitted.set()
        return result

    service.refresh_resource.side_effect = refresh
    runtime = await make_runtime(leader_enabled=True, poll_interval=0.05, leader_ttl=1)
    task = await create_watch(runtime)
    await asyncio.wait_for(submitted.wait(), 2)
    await drain(runtime)
    assert runtime.is_leader
    assert (await runtime.watch_manager.get_task(task.task_id)).last_status == "completed"
    service.refresh_resource.assert_awaited_once()


@pytest.mark.asyncio
async def test_busy_watches_do_not_hold_capacity(make_runtime, service):
    runtime = await make_runtime(max_concurrency=1)
    runtime.DUE_BATCH = 2
    busy = [await create_watch(runtime, f"viking://resources/busy{i}") for i in range(3)]
    free = await create_watch(runtime, "viking://resources/free")
    locks = runtime.watch_manager.locks
    async with (
        locks.tree("default", busy[0].to_uri),
        locks.tree("default", busy[1].to_uri),
        locks.tree("default", busy[2].to_uri),
    ):
        assert await runtime.dispatch_due() == 1
        await drain(runtime)
        for _ in range(3):
            assert await runtime.dispatch_due() == 0
    assert [call.kwargs["to"] for call in service.refresh_resource.await_args_list] == [free.to_uri]


@pytest.mark.asyncio
async def test_selected_run_executes_old_parameters_after_pause(make_runtime, service):
    runtime = await make_runtime()
    task = await create_watch(runtime, instruction="selected instruction")
    manager = runtime.watch_manager
    run = (await manager.due(1))[0]
    assert await manager.has_running_overlap("default", [TARGET])
    await manager.store.patch_task(
        task.task_id,
        WatchPatch(
            {
                "path": "https://example.org/next",
                "instruction": "next instruction",
                "is_active": False,
            }
        ),
    )
    receipt = await manager.execute(run)
    assert receipt is not None
    call = service.refresh_resource.await_args.kwargs
    assert call["path"] == task.path and call["instruction"] == "selected instruction"
    saved = await manager.get_task(task.task_id)
    assert saved.last_status == "completed"
    assert saved.instruction == "next instruction"
    assert not saved.is_active and saved.next_execution_time is None
    assert await manager.trigger(task.task_id) is not None


@pytest.mark.asyncio
async def test_stop_releases_selected_runs_before_submission(make_runtime, service, watch_tracker):
    runtime = await make_runtime(max_concurrency=2)
    await create_watch(runtime, "viking://resources/selected-a")
    await create_watch(runtime, "viking://resources/selected-b")
    assert await runtime.dispatch_due() == 2
    await runtime.stop(timeout=1)
    assert await runtime.watch_manager.store.running() == []
    assert await watch_tracker.list_tasks(**OWNER) == []
    service.refresh_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_is_bounded_by_concurrency(make_runtime, service, watch_tracker):
    release = asyncio.Event()

    async def refresh(**kwargs):
        await release.wait()
        record = await watch_tracker.create("add_resource", resource_id=kwargs["to"], **OWNER)
        await kwargs["on_task_created"](record)
        return {"task_id": record.task_id}

    service.refresh_resource.side_effect = refresh
    runtime = await make_runtime(max_concurrency=2, store_type=CountingFileWatchStore)
    for index in range(3):
        await create_watch(runtime, f"viking://resources/doc{index}")
    assert sum(await asyncio.gather(*[runtime.dispatch_due() for _ in range(3)])) == 2
    assert await runtime.dispatch_due() == 0
    release.set()
    await drain(runtime, sweep=False)
    records = await watch_tracker.list_tasks(**OWNER)
    assert len(records) == 2 and all(not record.is_terminal() for record in records)
    store = runtime.watch_manager.store
    reads = (store.running_reads, store.due_reads)
    for _ in range(3):
        assert await runtime.dispatch_due() == 0
    assert (store.running_reads, store.due_reads) == reads
    assert store.running_reads == 1
    restarted = runtime
    state = next(
        state
        for state in await restarted.watch_manager.store.running()
        if state.current_run_id == records[0].task_id
    )
    await watch_tracker.complete(records[0].task_id, {}, **OWNER)
    assert await restarted.watch_manager.settle(state.task_id, records[0].task_id)
    await restarted.sweep()
    assert sum(await asyncio.gather(*[restarted.dispatch_due() for _ in range(3)])) == 1
    await drain(restarted)
    assert await restarted.dispatch_due() == 0
    records = await watch_tracker.list_tasks(**OWNER)
    assert sum(not record.is_terminal() for record in records) == 2
    assert service.refresh_resource.await_count == 3

    # Explicit manual runs and first imports also occupy background capacity.
    manual = await create_watch(restarted, "viking://resources/manual", due=False)
    assert await restarted.watch_manager.trigger(manual.task_id)
    initial = await watch_tracker.create("add_resource", resource_id="initial", **OWNER)
    await create_watch(
        restarted,
        "viking://resources/initial",
        due=False,
        initial_run_id=initial.task_id,
    )
    waiting = await create_watch(restarted, "viking://resources/waiting")
    await restarted.sweep()
    assert await restarted.dispatch_due() == 0
    for record in await watch_tracker.list_tasks(**OWNER):
        if not record.is_terminal():
            await watch_tracker.complete(record.task_id, {}, **OWNER)
    await restarted.sweep()
    assert await restarted.dispatch_due() == 1
    await drain(restarted)
    assert (await restarted.watch_manager.store.get_state(waiting.task_id)).current_run_id
    await restarted.stop()
    await restarted.watch_manager.close()
    recovered = await make_runtime(max_concurrency=2, store_type=CountingFileWatchStore)
    assert await recovered.watch_manager.store.running() == []
    assert await recovered.dispatch_due() == 1
    await drain(recovered)


@pytest.mark.asyncio
async def test_missing_target_deactivates_without_refresh(make_runtime, service, watch_vfs):
    runtime = await make_runtime()
    task = await create_watch(runtime)
    await watch_vfs.rm(TARGET, recursive=True)
    await runtime.dispatch_due()
    await drain(runtime)
    task = await runtime.watch_manager.get_task(task.task_id)
    assert not task.is_active and "does not exist" in task.last_error
    service.refresh_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_task_is_settled_after_grace(make_runtime, watch_tracker):
    runtime = await make_runtime()
    task, _ = await running_watch(runtime, watch_tracker, missing=True)
    await runtime.sweep()
    assert await runtime.watch_manager.has_running_overlap("default", [TARGET])
    await shape_watch(runtime.watch_manager.store, task.task_id, run_started_at=1.0)
    await runtime.sweep()
    assert not await runtime.watch_manager.has_running_overlap("default", [TARGET])
    saved = await runtime.watch_manager.get_task(task.task_id)
    assert saved.last_status == "error" and saved.next_execution_time is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, ConnectionError("store unavailable")])
async def test_overdue_run_stays_occupied_until_its_task_stops(
    make_runtime, watch_tracker, monkeypatch, failure
):
    runtime = await make_runtime()
    task, record = await running_watch(runtime, watch_tracker)
    await runtime.sweep()
    assert not (await watch_tracker.get(record.task_id, **OWNER)).is_terminal()
    await shape_watch(runtime.watch_manager.store, task.task_id, run_started_at=1.0)
    cancel = watch_tracker.cancel
    if failure is not None:
        monkeypatch.setattr(watch_tracker, "cancel", AsyncMock(side_effect=failure))
        await runtime.sweep()
        assert await runtime.watch_manager.has_running_overlap("default", [TARGET])
        assert not (await watch_tracker.get(record.task_id, **OWNER)).is_terminal()
        monkeypatch.setattr(watch_tracker, "cancel", cancel)
    await runtime.sweep()
    assert not await runtime.watch_manager.has_running_overlap("default", [TARGET])
    assert (await runtime.watch_manager.get_task(task.task_id)).last_status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("active", [False, True])
async def test_overdue_connector_work_is_cancelled_and_watch_is_failed(
    make_runtime, watch_tracker, started, active
):
    runtime = await make_runtime()
    task, record = await running_watch(runtime, watch_tracker, task_type="connector_import")
    await runtime.watch_manager.update_task(task.task_id, **OWNER, role="root", is_active=active)
    if started:
        await watch_tracker.start(record.task_id, **OWNER)
        await watch_tracker.register_work(record.task_id, "external", "ExternalTask", **OWNER)
        await watch_tracker.start_work(record.task_id, "external", **OWNER)
    await runtime.sweep()
    assert await runtime.watch_manager.has_running_overlap("default", [TARGET])
    assert (await watch_tracker.get(record.task_id, **OWNER)).error is None

    await shape_watch(runtime.watch_manager.store, task.task_id, run_started_at=1.0)
    await runtime.sweep()
    cancelled = await watch_tracker.get(record.task_id, **OWNER)
    assert cancelled.status.value == ("cancelling" if started else "cancelled")
    assert cancelled.error is None
    state = await runtime.watch_manager.store.get_state(task.task_id)
    assert state.current_run_id is None and state.run_started_at is None
    assert state.task.last_task_id == record.task_id
    assert state.task.last_status == "failed"
    assert state.task.last_error == "Watch run Task timed out after 10800s"
    assert state.task.is_active is active
    assert (state.task.next_execution_time is not None) is active
    assert not await runtime.watch_manager.has_running_overlap("default", [TARGET])
    assert task.task_id not in runtime._active_runs

    if started:
        await watch_tracker.mark_work_done(record.task_id, "external", **OWNER)
    saved = state.task.to_dict()
    await runtime.sweep()
    await watch_tracker.complete(record.task_id, {"late": "result"}, **OWNER)
    assert (await watch_tracker.get(record.task_id, **OWNER)).status.value == "cancelled"
    assert (await runtime.watch_manager.get_task(task.task_id)).to_dict() == saved


@pytest.mark.asyncio
async def test_timeout_failure_does_not_overwrite_concurrent_completion(
    make_runtime, watch_tracker, monkeypatch
):
    runtime = await make_runtime()
    task, record = await running_watch(runtime, watch_tracker, task_type="connector_import")
    await shape_watch(runtime.watch_manager.store, task.task_id, run_started_at=1.0)
    cancel = watch_tracker.cancel

    async def complete_before_cancel(task_id, **owner):
        await watch_tracker.complete(task_id, {"completed": True}, **owner)
        return await cancel(task_id, **owner)

    monkeypatch.setattr(watch_tracker, "cancel", complete_before_cancel)
    await runtime.sweep()
    completed = await watch_tracker.get(record.task_id, **OWNER)
    assert completed.status.value == "completed" and completed.error is None
    assert completed.result == {"completed": True}
    state = await runtime.watch_manager.store.get_state(task.task_id)
    assert state.current_run_id is None
    assert state.task.last_status == "completed" and state.task.last_error is None
    assert not await runtime.watch_manager.has_running_overlap("default", [TARGET])


@pytest.mark.asyncio
async def test_nonterminal_task_keeps_occupancy_after_timeout(make_runtime, watch_tracker):
    runtime = await make_runtime()
    task, record = await running_watch(runtime, watch_tracker)
    await watch_tracker.register_work(record.task_id, "remote", "external", **OWNER)
    await watch_tracker.start_work(record.task_id, "remote", **OWNER)
    await shape_watch(runtime.watch_manager.store, task.task_id, run_started_at=1.0)
    await runtime.sweep()
    assert await runtime.watch_manager.has_running_overlap("default", [TARGET])
    await watch_tracker.mark_work_done(record.task_id, "remote", **OWNER)
    await runtime.sweep()
    assert not await runtime.watch_manager.has_running_overlap("default", [TARGET])
    assert (await runtime.watch_manager.get_task(task.task_id)).last_status == "cancelled"


@pytest.mark.asyncio
async def test_delete_tasks_cancels_a_connector_run_task(make_runtime, watch_tracker):
    runtime = await make_runtime()
    task, record = await running_watch(runtime, watch_tracker, task_type="connector_import")
    await runtime.watch_manager.delete_tasks(**OWNER)
    assert await runtime.watch_manager.get_task(task.task_id) is None
    assert (await watch_tracker.get(record.task_id, **OWNER)).status.value == "cancelled"
    assert not await runtime.watch_manager.has_running_overlap("default", [TARGET])


@pytest.mark.asyncio
async def test_restart_runs_only_watches_that_came_due(make_runtime, service):
    first = await make_runtime()
    overdue = await create_watch(first, "viking://resources/overdue")
    future = await create_watch(first, "viking://resources/future", due=False)
    paused = await create_watch(first, "viking://resources/paused", is_active=False)
    await first.stop()
    await first.watch_manager.close()
    second = await make_runtime()
    assert await second.dispatch_due() == 1
    await drain(second)
    tasks = await second.watch_manager.get_all_tasks("default", "default", "root")
    assert {task.task_id for task in tasks} == {overdue.task_id, future.task_id, paused.task_id}
    ran = await second.watch_manager.get_task(overdue.task_id)
    assert ran.last_status == "completed"
    assert ran.next_execution_time == ran.last_execution_time + timedelta(minutes=5)
    assert await second.dispatch_due() == 0
    service.refresh_resource.assert_awaited_once()


@pytest.mark.asyncio
async def test_permanent_feishu_refresh_failure_deactivates(make_runtime, service, monkeypatch):
    async def refresh(_token):
        raise FeishuTokenRefreshError("invalid client credentials", permanent=True)

    monkeypatch.setattr(
        FeishuOAuthClient,
        "from_auth_state",
        lambda *_args, **_kwargs: SimpleNamespace(refresh_user_access_token=refresh),
    )
    runtime = await make_runtime()

    async def resolve_account(_account_id, resolver):
        return resolver(
            SimpleNamespace(
                account=SimpleNamespace(feishu=None),
                cluster=SimpleNamespace(feishu=FeishuConfig()),
            )
        )

    runtime.watch_manager._runtime_config_manager = SimpleNamespace(resolve_account=resolve_account)
    task = await create_watch(
        runtime,
        path="https://example.feishu.cn/docx/doc",
        auth_state={
            "provider": "feishu",
            "access_token": "old",
            "refresh_token": "refresh",
            "expires_at": None,
            "app_id": "app",
            "app_secret": "secret",
        },
    )
    assert await runtime.watch_manager.trigger(task.task_id) is None
    task = await runtime.watch_manager.get_task(task.task_id)
    assert not task.is_active and task.last_error == "invalid client credentials"
    service.refresh_resource.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [GIT_AUTH_FAILED, "UNKNOWN"])
async def test_git_watch_pauses_only_on_auth_failure(make_runtime, watch_tracker, code):
    runtime = await make_runtime()
    task, record = await running_watch(runtime, watch_tracker)
    await watch_tracker.fail(record.task_id, "Git failed", result={"code": code}, **OWNER)
    await runtime.sweep()
    saved = await runtime.watch_manager.get_task(task.task_id)
    assert saved.is_active is (code != GIT_AUTH_FAILED)
    assert (saved.next_execution_time is None) is (code == GIT_AUTH_FAILED)


class TestWatchTimestampNormalization:
    @pytest.mark.asyncio
    async def test_persisted_aware_due_time_does_not_break_due_check(self, watch_vfs):
        """Offset-aware persisted due times must not crash the due check (#3268)."""
        aware_past = (datetime.now(timezone.utc) - timedelta(minutes=10)).astimezone(
            timezone(timedelta(hours=8))
        )
        document = {
            "tasks": [
                {
                    "task_id": "aware-task",
                    "path": "/test/aware",
                    "to_uri": "viking://resources/aware",
                    "watch_interval": 60.0,
                    "created_at": (
                        datetime.now(timezone.utc) - timedelta(hours=1)
                    ).isoformat(),
                    "next_execution_time": aware_past.isoformat(),
                    "is_active": True,
                }
            ],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await watch_vfs.write_file(
            FileWatchStore.STORAGE_URI,
            json.dumps(document),
            ctx=FileWatchStore._ctx(),
            auto_pathlock=False,
        )

        store = FileWatchStore(watch_vfs)
        await store.initialize()
        try:
            candidates = await store.due_candidates(10)
        finally:
            await store.close()

        assert [candidate.task_id for candidate in candidates] == ["aware-task"]
