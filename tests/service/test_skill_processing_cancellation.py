# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.service.task_store import PersistentTaskStore, WorkRegistration
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.service.task_work_index import get_task_context
from openviking.telemetry import (
    OperationTelemetry,
    bind_telemetry,
)
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "fail"])
async def test_skill_rollback_persists_cancellation_for_finished_work(monkeypatch, outcome):
    store = PersistentTaskStore(_FakeAgfs())
    tracker = TaskTracker(store=store)
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    task = await tracker.create("add_skill", **owner)
    await getattr(tracker, outcome)(
        task.task_id, {} if outcome == "complete" else "failed", **owner
    )
    with pytest.raises(ValueError, match="already"):
        await tracker.cancel(task.task_id, **owner)
    await ResourceService.__new__(ResourceService).cancel_skill_processing(task.task_id, ctx)
    restarted = TaskTracker(store=store)
    restored = await restarted.get(task.task_id, **owner)
    assert restored.status == TaskStatus.CANCELLED
    assert await restarted.is_cancellation_requested(task.task_id, **owner)
    if outcome == "fail":
        assert restored.error == "failed"


@pytest.mark.asyncio
async def test_skill_rollback_prevents_late_ack_failure_from_replaying_new_index(monkeypatch):
    tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    task = await tracker.create("add_skill", **owner)
    assert (
        await tracker.register_work(task.task_id, "last-vector", "Embedding", **owner)
        is WorkRegistration.CREATED
    )
    await tracker.complete(task.task_id, {}, **owner)
    await tracker.mark_work_done(task.task_id, "last-vector", **owner)
    assert (await tracker.get(task.task_id, **owner)).status == TaskStatus.COMPLETED
    await ResourceService.__new__(ResourceService).cancel_skill_processing(task.task_id, ctx)
    assert await tracker.is_cancellation_requested(task.task_id, **owner)
    assert (
        await tracker.register_work(task.task_id, "last-vector", "Embedding", **owner)
        is WorkRegistration.REJECTED
    )


@pytest.mark.asyncio
async def test_cancel_during_skill_task_creation_settles_persisted_record(monkeypatch):
    store = PersistentTaskStore(_FakeAgfs())
    started, resume = asyncio.Event(), asyncio.Event()
    original_create = store.create

    async def slow_create(task, **kwargs):
        started.set()
        await resume.wait()
        await original_create(task, **kwargs)

    monkeypatch.setattr(store, "create", slow_create)
    tracker = TaskTracker(store=store)
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    service = ResourceService.__new__(ResourceService)
    service._resource_processor = object()
    service._viking_fs = object()
    service._skill_processor = SimpleNamespace(process_skill=AsyncMock())
    with bind_telemetry(OperationTelemetry("cancel-skill-create")):
        adding = asyncio.create_task(
            service.add_skill(
                "body", ctx, wait=True, target_uri="viking://agent/skills", task_id="create-test"
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 1)
            adding.cancel()
            resume.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(adding, 1)
            record = await tracker.get("create-test", **owner)
            assert record.status == TaskStatus.FAILED
            assert (await store.get_task("create-test", **owner)).status is TaskStatus.FAILED
            assert not await tracker.has_work(record.task_id, **owner)
            service._skill_processor.process_skill.assert_not_awaited()
        finally:
            resume.set()
            await asyncio.gather(adding, return_exceptions=True)


@pytest.mark.asyncio
async def test_skill_success_remains_completed_when_queue_work_finishes_first(monkeypatch):
    tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}

    async def process_skill(**kwargs):
        task_id = get_task_context().task_id
        assert (
            await tracker.register_work(task_id, "vector", "Embedding", **owner)
            is WorkRegistration.CREATED
        )
        await tracker.mark_work_done(task_id, "vector", **owner)
        return {"uri": "viking://agent/skills/demo"}

    service = ResourceService.__new__(ResourceService)
    service._resource_processor = object()
    service._viking_fs = object()
    service._skill_processor = SimpleNamespace(process_skill=process_skill)
    service._background_tasks = set()

    with bind_telemetry(OperationTelemetry("add-skill")):
        result = await service.add_skill("body", ctx, wait=True, target_uri="viking://agent/skills")

    task = await tracker.get(result["task_id"], **owner)
    assert task.status is TaskStatus.COMPLETED
    assert task.error is None
    assert task.result["root_uri"] == "viking://agent/skills/demo"


@pytest.mark.asyncio
async def test_skill_failure_preserves_business_error_without_queue_work(monkeypatch):
    tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}

    async def process_skill(**kwargs):
        raise RuntimeError("skill processing failed")

    service = ResourceService.__new__(ResourceService)
    service._resource_processor = object()
    service._viking_fs = object()
    service._skill_processor = SimpleNamespace(process_skill=process_skill)

    with bind_telemetry(OperationTelemetry("add-skill")):
        with pytest.raises(RuntimeError, match="skill processing failed"):
            await service.add_skill("body", ctx, wait=True, target_uri="viking://agent/skills")

    tasks = await tracker.list_tasks(**owner)
    assert len(tasks) == 1
    assert tasks[0].status is TaskStatus.FAILED
    assert tasks[0].error == "skill processing failed"
