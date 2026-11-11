# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Submission, target locks and restart observation with persistent Tasks."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_runtime import WatchRuntime
from openviking.resource.watch_store_file import FileWatchStore
from openviking.service.resource_service import ResourceService
from openviking.utils.git_auth import GIT_AUTH_FAILED, raise_git_auth_error
from openviking_cli.exceptions import OpenVikingError
from tests.utils.watch_fixtures import reserve_watch, shape_watch

TARGET = "viking://resources/doc"
OWNER = {"account_id": "default", "user_id": "default"}


@pytest_asyncio.fixture
async def assembly(watch_vfs, watch_tracker):
    tracker = watch_tracker

    async def refresh(**kwargs):
        record = await tracker.create("add_resource", resource_id=kwargs["to"], **OWNER)
        await kwargs["on_task_created"](record)
        return {"status": "success", "task_id": record.task_id}

    resources = SimpleNamespace(refresh_resource=AsyncMock(side_effect=refresh), _connector=None)
    service = WatchManager(
        FileWatchStore(watch_vfs),
        viking_fs=watch_vfs,
        tracker=tracker,
        resource_service=resources,
    )
    await service.initialize()
    await watch_vfs.mkdir(TARGET, exist_ok=True)
    runtime = WatchRuntime(service, leader_enabled=False)
    yield SimpleNamespace(service=service, runtime=runtime, resources=resources, tracker=tracker)
    await runtime.stop()
    await service.close()


async def seed(a, **values):
    return await a.service.create_task(
        path="https://example.org/doc", to_uri=TARGET, watch_interval=1, **values
    )


@pytest.mark.asyncio
async def test_api_submits_real_task_and_concurrent_duplicates_reuse_it(assembly):
    a = assembly
    task = await seed(a, is_active=False)
    receipts = await asyncio.gather(*[a.service.trigger(task.task_id) for _ in range(3)])
    assert all(r for r in receipts)
    assert len({r.task_id for r in receipts}) == 1
    assert sum(not r.reused for r in receipts) == 1
    record = await a.tracker.get(receipts[0].task_id, **OWNER)
    assert record.task_type == "add_resource" and record.resource_id == TARGET
    assert not record.is_terminal()
    a.resources.refresh_resource.assert_awaited_once()
    assert not a.runtime.is_running
    assert len(await a.tracker.list_tasks(**OWNER)) == 1


@pytest.mark.asyncio
async def test_foreign_event_loops_reuse_one_actual_task(assembly):
    a = assembly
    task = await seed(a, is_active=False)
    original = a.resources.refresh_resource.side_effect

    async def refresh(**kwargs):
        await asyncio.sleep(0.05)
        return await original(**kwargs)

    a.resources.refresh_resource.side_effect = refresh

    def trigger():
        return asyncio.run(a.service.trigger(task.task_id))

    receipts = await asyncio.gather(*[asyncio.to_thread(trigger) for _ in range(3)])
    assert all(receipts)
    assert len({r.task_id for r in receipts}) == 1
    assert sum(not r.reused for r in receipts) == 1
    a.resources.refresh_resource.assert_awaited_once()
    assert len(await a.tracker.list_tasks(**OWNER)) == 1


@pytest.mark.asyncio
async def test_submission_releases_target_lock_and_keeps_move_occupancy(assembly):
    a = assembly
    task = await seed(a)
    original = a.resources.refresh_resource.side_effect

    async def refresh(**kwargs):
        state = await a.service.store.get_state(task.task_id)
        assert state.current_run_id is None and state.run_started_at is not None
        assert await a.service.has_running_overlap("default", [TARGET])
        async with a.service.locks.tree("default", TARGET):
            pass
        return await original(**kwargs)

    a.resources.refresh_resource.side_effect = refresh
    assert await a.service.trigger(task.task_id)


@pytest.mark.asyncio
async def test_first_task_is_only_observed(assembly):
    a = assembly
    record = await a.tracker.create("add_resource", resource_id=TARGET, **OWNER)
    task = await seed(a, initial_run_id=record.task_id)
    await a.runtime.start()
    assert await a.runtime.dispatch_due() == 0
    await a.tracker.complete(record.task_id, {}, **OWNER)
    await a.runtime.sweep()
    assert (await a.service.get_task(task.task_id)).last_status == "completed"
    a.resources.refresh_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_before_task_creation_releases_submission(assembly):
    a = assembly
    task = await seed(a)
    a.resources.refresh_resource.side_effect = ValueError("invalid source")
    with pytest.raises(ValueError, match="invalid source"):
        await a.service.trigger(task.task_id)
    state = await a.service.store.get_state(task.task_id)
    assert state.run_started_at is None and state.current_run_id is None
    assert state.task.last_error == "invalid source"
    assert await a.tracker.list_tasks(**OWNER) == []


@pytest.mark.asyncio
async def test_failure_after_task_creation_settles_actual_task(assembly):
    a = assembly
    task = await seed(a)
    original = a.resources.refresh_resource.side_effect

    async def fail(**kwargs):
        await original(**kwargs)
        raise ConnectionError("submission failed")

    a.resources.refresh_resource.side_effect = fail
    with pytest.raises(ConnectionError):
        await a.service.trigger(task.task_id)
    state = await a.service.store.get_state(task.task_id)
    assert state.run_started_at is None
    assert state.task.last_status == "failed" and state.task.last_task_id
    assert (await a.tracker.get(state.task.last_task_id, **OWNER)).is_terminal()


@pytest.mark.asyncio
async def test_interrupted_start_expires_and_allows_next_submission(assembly):
    a = assembly
    task = await seed(a)
    await reserve_watch(a.service.store, task.task_id, target_uri=TARGET, manual=True)
    await shape_watch(a.service.store, task.task_id, run_started_at=1.0)
    await a.runtime.start()
    await a.runtime.sweep()
    receipt = await a.service.trigger(task.task_id)
    assert receipt
    assert (await a.service.store.get_state(task.task_id)).current_run_id == receipt.task_id


@pytest.mark.asyncio
async def test_identity_cleanup_without_runtime_and_single_shot_settlement(assembly):
    a = assembly
    task = await seed(a)
    receipt = await a.service.trigger(task.task_id)
    assert not await a.service.settle(task.task_id, receipt.task_id)
    await a.service.delete_tasks("default", "default")
    assert await a.service.get_task(task.task_id) is None
    assert (await a.tracker.get(receipt.task_id, **OWNER)).is_terminal()
    assert not await a.service.settle(task.task_id, receipt.task_id)
    assert await a.service.store.running() == []


@pytest.mark.asyncio
async def test_completed_duplicate_submits_next_real_task(assembly):
    a = assembly
    task = await seed(a, is_active=False)
    first = await a.service.trigger(task.task_id)
    await a.tracker.complete(first.task_id, {}, **OWNER)
    second = await a.service.trigger(task.task_id)
    assert second.task_id != first.task_id and not second.reused
    assert not (await a.service.get_task(task.task_id)).is_active


@pytest.mark.asyncio
async def test_runtime_shutdown_waits_for_inflight_submission(assembly):
    a = assembly
    task = await seed(a)
    await shape_watch(a.service.store, task.task_id, next_run_at=1.0)
    entered, release = asyncio.Event(), asyncio.Event()
    original = a.resources.refresh_resource.side_effect

    async def slow(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    a.resources.refresh_resource.side_effect = slow
    await a.runtime.start()
    assert await a.runtime.dispatch_due() == 1
    await entered.wait()
    with pytest.raises(TimeoutError, match="keep dependencies open"):
        await a.runtime.stop(timeout=0.01)
    release.set()
    await a.runtime.stop(timeout=1)
    assert (await a.service.store.get_state(task.task_id)).current_run_id


@pytest.mark.asyncio
async def test_missing_source_deactivates_without_submission(assembly):
    a = assembly
    task = await seed(a)
    await a.service.update_task(task.task_id, **OWNER, role="root", path="/missing/watch-source")
    assert await a.service.trigger(task.task_id) is None
    state = await a.service.store.get_state(task.task_id)
    assert not state.task.is_active
    assert state.task.last_status == "failed"
    assert "Resource path does not exist" in state.task.last_error
    assert state.run_started_at is None
    a.resources.refresh_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_without_task_is_rejected_and_releases_occupancy(assembly):
    a = assembly
    task = await seed(a)
    a.resources.refresh_resource.side_effect = None
    a.resources.refresh_resource.return_value = {"status": "success"}
    with pytest.raises(RuntimeError, match="did not create a Task"):
        await a.service.trigger(task.task_id)
    state = await a.service.store.get_state(task.task_id)
    assert state.run_started_at is None
    assert state.task.last_status == "failed"


@pytest.mark.asyncio
async def test_stale_terminal_snapshot_cannot_settle_new_run(assembly):
    a = assembly
    task = await seed(a)
    first = await a.service.trigger(task.task_id)
    old_run = a.service.run(await a.service.store.get_state(task.task_id))
    await a.tracker.complete(first.task_id, {}, **OWNER)
    old_record = await a.tracker.get(first.task_id, **OWNER)
    second = await a.service.trigger(task.task_id)
    assert not await a.service.settle_record(old_run, old_record)
    state = await a.service.store.get_state(task.task_id)
    assert state.current_run_id == second.task_id
    assert state.run_started_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("permanent", [False, True])
async def test_git_preflight_failure_pauses_only_for_auth_failure(
    assembly, watch_vfs, monkeypatch, permanent
):
    a = assembly
    resources = ResourceService(
        vikingdb=object(),
        viking_fs=watch_vfs,
        resource_processor=SimpleNamespace(github_token_for=AsyncMock(return_value=None)),
        skill_processor=object(),
    )
    monkeypatch.setattr(resources._connector, "should_delegate", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "openviking.service.resource_service.require_remote_resource_source", lambda source: source
    )

    async def preflight(*args, **kwargs):
        if permanent:
            raise_git_auth_error(b"fatal: Authentication failed")
        raise ConnectionError("Git preflight connection timed out")

    monkeypatch.setattr(resources, "_preflight_git_source", preflight)
    a.service._resource_service = resources
    task = await seed(a, original_role="root")
    await a.service.update_task(
        task.task_id, **OWNER, role="root", path="https://github.com/org/private.git"
    )
    expected = OpenVikingError if permanent else ConnectionError
    with pytest.raises(expected) as error:
        await a.service.trigger(task.task_id)
    if permanent:
        assert error.value.code == GIT_AUTH_FAILED
    state = await a.service.store.get_state(task.task_id)
    assert state.task.is_active is (not permanent)
    assert (state.task.next_execution_time is None) is permanent
    assert state.task.last_status == "failed"
    assert state.run_started_at is None
    assert await a.tracker.list_tasks(**OWNER) == []
