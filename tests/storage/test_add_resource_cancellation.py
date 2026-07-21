# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker, set_task_tracker
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking.storage.queuefs.semantic_dag import DagWork, SemanticDagExecutor, VectorizeTask
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


def test_add_resource_message_persists_target_ownership_for_safe_rollback():
    msg = AddResourceMsg(
        task_id="task-1",
        root_uri="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        role="user",
        path="https://example.com/demo.git",
        target_created=True,
    )

    restored = AddResourceMsg.from_dict(msg.to_dict())

    assert restored.target_created is True


def test_legacy_add_resource_message_treats_target_ownership_as_unknown():
    restored = AddResourceMsg.from_dict(
        {
            "task_id": "task-1",
            "root_uri": "viking://resources/demo",
            "account_id": "acme",
            "user_id": "alice",
            "role": "user",
            "path": "https://example.com/demo.git",
        }
    )

    assert restored.target_created is None


def test_truthy_non_boolean_target_ownership_never_grants_delete_authority():
    payload = {
        "task_id": "task-1",
        "root_uri": "viking://resources/demo",
        "account_id": "acme",
        "user_id": "alice",
        "role": "user",
        "path": "https://example.com/demo.git",
        "target_created": "false",
    }

    assert AddResourceMsg.from_dict(payload).target_created is None


def test_add_resource_message_propagates_task_to_semantic_pipeline():
    msg = SemanticMsg(
        uri="viking://resources/demo",
        context_type="resource",
        source_task_id="task-1",
    )

    restored = SemanticMsg.from_dict(msg.to_dict())

    assert restored.source_task_id == "task-1"


@pytest.mark.asyncio
async def test_cancel_add_resource_is_owner_scoped_and_idempotent():
    tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    service = ResourceService()
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    task = await tracker.create(
        "add_resource",
        resource_id="viking://resources/demo",
        account_id="acme",
        user_id="alice",
    )
    await tracker.start(task.task_id, account_id="acme", user_id="alice")

    bob_ctx = RequestContext(user=UserIdentifier("acme", "bob"), role=Role.USER)
    with pytest.raises(NotFoundError):
        await service.cancel_add_resource_task(task.task_id, ctx=bob_ctx)
    still_running = await tracker.get(task.task_id, account_id="acme", user_id="alice")
    assert still_running is not None
    assert still_running.status == TaskStatus.RUNNING

    first = await service.cancel_add_resource_task(task.task_id, ctx=ctx)
    second = await service.cancel_add_resource_task(task.task_id, ctx=ctx)

    assert first["status"] == TaskStatus.CANCELLED.value
    assert second == first


@pytest.mark.asyncio
async def test_root_cancel_loads_persisted_task_from_explicit_owner_scope():
    agfs = _FakeAgfs()
    original_tracker = TaskTracker(store=PersistentTaskStore(agfs))
    task = await original_tracker.create(
        "add_resource",
        resource_id="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        task_id="task-1",
    )
    set_task_tracker(TaskTracker(store=PersistentTaskStore(agfs)))
    service = ResourceService()
    root_ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ROOT)

    cancelled = await service.cancel_add_resource_task(task.task_id, ctx=root_ctx)

    assert cancelled["status"] == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_default_root_identity_can_cancel_cached_tenant_task():
    tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    task = await tracker.create(
        "add_resource",
        resource_id="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        task_id="task-1",
    )
    service = ResourceService()
    root_ctx = RequestContext(user=UserIdentifier("default", "default"), role=Role.ROOT)

    cancelled = await service.cancel_add_resource_task(task.task_id, ctx=root_ctx)

    assert cancelled["status"] == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_explicit_target_ownership_is_read_after_lifecycle_lock(monkeypatch):
    service = ResourceService()
    resource_lock = MagicMock()
    resource_lock.active = True
    resource_processor = MagicMock()
    resource_processor.tree_builder.resolve_target_uri = AsyncMock(
        return_value=("viking://resources/demo", None)
    )
    lifecycle_lock_acquired = False

    async def acquire_lock(*_args, **_kwargs):
        nonlocal lifecycle_lock_acquired
        lifecycle_lock_acquired = True
        return resource_lock

    async def read_target_ownership(*_args, **_kwargs):
        assert lifecycle_lock_acquired
        return False

    resource_processor.acquire_resource_lock = AsyncMock(side_effect=acquire_lock)
    resource_processor.target_contains_preexisting_data = AsyncMock(
        side_effect=read_target_ownership
    )
    service._resource_processor = resource_processor
    service._viking_fs = MagicMock()
    service._viking_fs.exists = AsyncMock(return_value=False)
    service._viking_fs._uri_to_path.return_value = "/local/acme/resources/demo"
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: object(),
    )

    (
        root_uri,
        acquired_lock,
        target_preexisting,
        target_created,
    ) = await service._plan_resource_target(
        path="https://example.com/demo.git",
        ctx=RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER),
        target=SimpleNamespace(to="viking://resources/demo", parent=None, create_parent=False),
        source_name=None,
        source_info=SimpleNamespace(
            source_name="demo",
            source_path="https://example.com/demo.git",
            source_format="repository",
        ),
    )

    assert root_uri == "viking://resources/demo"
    assert acquired_lock is resource_lock
    assert target_preexisting is False
    assert target_created is True
    resource_processor.target_contains_preexisting_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_preexisting_empty_target_is_never_marked_task_created(monkeypatch):
    service = ResourceService()
    resource_lock = MagicMock()
    resource_processor = MagicMock()
    resource_processor.tree_builder.resolve_target_uri = AsyncMock(
        return_value=("viking://resources/demo", None)
    )
    resource_processor.acquire_resource_lock = AsyncMock(return_value=resource_lock)
    resource_processor.target_contains_preexisting_data = AsyncMock(return_value=False)
    service._resource_processor = resource_processor
    service._viking_fs = MagicMock()
    service._viking_fs.exists = AsyncMock(return_value=True)
    service._viking_fs._uri_to_path.return_value = "/local/acme/resources/demo"
    monkeypatch.setattr("openviking.storage.transaction.get_lock_manager", lambda: object())

    _, _, target_preexisting, target_created = await service._plan_resource_target(
        path="https://example.com/demo.git",
        ctx=RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER),
        target=SimpleNamespace(to="viking://resources/demo", parent=None, create_parent=False),
        source_name="demo",
        source_info=SimpleNamespace(
            source_name="demo",
            source_path="https://example.com/demo.git",
            source_format="repository",
        ),
    )

    assert target_preexisting is False
    assert target_created is False


@pytest.mark.asyncio
@pytest.mark.parametrize("target_created", [False, None])
async def test_cancel_rollback_never_deletes_preexisting_or_unknown_target(target_created):
    service = ResourceService()
    service._viking_fs = AsyncMock()
    msg = AddResourceMsg(
        task_id="task-1",
        root_uri="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        role="user",
        path="https://example.com/demo.git",
        target_created=target_created,
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    await service.rollback_cancelled_add_resource(msg, ctx=ctx)

    service._viking_fs.rm.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_rollback_deletes_only_task_created_target():
    service = ResourceService()
    service._viking_fs = AsyncMock()
    msg = AddResourceMsg(
        task_id="task-1",
        root_uri="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        role="user",
        path="https://example.com/demo.git",
        target_created=True,
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    resource_lock = MagicMock()
    resource_lock.handle = "lock-handle"

    await service.rollback_cancelled_add_resource(msg, ctx=ctx, resource_lock=resource_lock)

    service._viking_fs.rm.assert_awaited_once_with(
        "viking://resources/demo",
        recursive=True,
        ctx=ctx,
        lock_handle="lock-handle",
    )


@pytest.mark.asyncio
async def test_cancel_rollback_rejects_truthy_non_boolean_prepared_ownership():
    service = ResourceService()
    service._viking_fs = AsyncMock()
    msg = AddResourceMsg(
        task_id="task-1",
        root_uri="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        role="user",
        target_created=True,
        prepared={"target_created": "false"},
    )

    await service.rollback_cancelled_add_resource(
        msg,
        ctx=RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER),
    )

    service._viking_fs.rm.assert_not_awaited()


@pytest.mark.asyncio
async def test_queued_cancelled_job_is_acked_and_rolled_back_without_execution():
    agfs = _FakeAgfs()
    tracker = TaskTracker(store=PersistentTaskStore(agfs))
    set_task_tracker(tracker)
    task = await tracker.create(
        "add_resource",
        resource_id="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        task_id="task-1",
    )
    await tracker.cancel(task.task_id, account_id="acme", user_id="alice")
    set_task_tracker(TaskTracker(store=PersistentTaskStore(agfs)))
    service = MagicMock()
    service.rollback_cancelled_add_resource = AsyncMock()
    service.execute_add_resource_job = AsyncMock()
    processor = AddResourceProcessor(service, asyncio.get_running_loop())
    resource_lock = MagicMock()
    resource_lock.close = AsyncMock()
    processor._load_lock = AsyncMock(return_value=resource_lock)
    rollback_saw_active_lock = False

    async def rollback(*_args, **_kwargs):
        nonlocal rollback_saw_active_lock
        rollback_saw_active_lock = resource_lock.close.await_count == 0

    service.rollback_cancelled_add_resource.side_effect = rollback
    msg = AddResourceMsg(
        task_id=task.task_id,
        root_uri="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        role="user",
        path="https://example.com/demo.git",
        target_created=True,
    )

    await processor._process(msg, msg.to_dict())

    service.execute_add_resource_job.assert_not_awaited()
    assert rollback_saw_active_lock
    resource_lock.close.assert_awaited_once()
    service.rollback_cancelled_add_resource.assert_awaited_once_with(
        msg,
        ctx=ANY,
        resource_lock=resource_lock,
    )


@pytest.mark.asyncio
async def test_post_execution_cancel_rolls_back_without_completing():
    tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    service = MagicMock()
    service.rollback_cancelled_add_resource = AsyncMock()
    msg = AddResourceMsg(
        task_id="task-1",
        root_uri="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        role="user",
        target_created=True,
    )

    async def execute(*_args, **_kwargs):
        await tracker.cancel(msg.task_id, account_id="acme", user_id="alice")
        return {"status": "success"}

    service.execute_add_resource_job = AsyncMock(side_effect=execute)
    processor = AddResourceProcessor(service, asyncio.get_running_loop())

    await processor._process(msg, msg.to_dict())

    task = await tracker.get(msg.task_id, account_id="acme", user_id="alice")
    assert task is not None
    assert task.status == TaskStatus.CANCELLED
    service.rollback_cancelled_add_resource.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_racing_with_completion_still_rolls_back():
    tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    service = MagicMock()
    service.rollback_cancelled_add_resource = AsyncMock()
    service.execute_add_resource_job = AsyncMock(
        return_value={"status": "success", "root_uri": "viking://resources/demo"}
    )
    msg = AddResourceMsg(
        task_id="task-1",
        root_uri="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        role="user",
        path="https://example.com/demo.git",
        target_created=True,
    )
    complete = tracker.complete

    async def cancel_before_complete(*args, **kwargs):
        await tracker.cancel(msg.task_id, account_id="acme", user_id="alice")
        return await complete(*args, **kwargs)

    tracker.complete = cancel_before_complete
    processor = AddResourceProcessor(service, asyncio.get_running_loop())

    await processor._process(msg, msg.to_dict())

    task = await tracker.get(msg.task_id, account_id="acme", user_id="alice")
    assert task is not None
    assert task.status == TaskStatus.CANCELLED
    service.rollback_cancelled_add_resource.assert_awaited_once_with(
        msg,
        ctx=ANY,
        resource_lock=None,
    )


@pytest.mark.asyncio
async def test_deferred_target_resolution_updates_rollback_ownership():
    service = ResourceService()
    service.add_resource = AsyncMock(
        return_value={
            "status": "success",
            "root_uri": "viking://resources/resolved",
            "_target_created": True,
        }
    )
    msg = AddResourceMsg(
        task_id="task-1",
        root_uri="viking://resources/placeholder",
        account_id="acme",
        user_id="alice",
        role="user",
        path="https://example.larkoffice.com/docx/token",
        defer_target_resolution=True,
    )

    result = await service.execute_add_resource_job(
        msg,
        ctx=RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER),
        resource_lock=None,
        stage_callback=AsyncMock(),
    )

    assert result == {"status": "success", "root_uri": "viking://resources/resolved"}
    assert msg.root_uri == "viking://resources/resolved"
    assert msg.target_created is True


@pytest.mark.asyncio
async def test_running_job_rolls_back_instead_of_completing_after_cancel():
    tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    service = MagicMock()
    service.rollback_cancelled_add_resource = AsyncMock()
    continued_after_cancel = False
    msg = AddResourceMsg(
        task_id="task-1",
        root_uri="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        role="user",
        path="https://example.com/demo.git",
        target_created=True,
    )

    async def execute(*_args, **kwargs):
        nonlocal continued_after_cancel
        await tracker.cancel(msg.task_id, account_id="acme", user_id="alice")
        await kwargs["stage_callback"]("parsing")
        continued_after_cancel = True
        return {"status": "success"}

    service.execute_add_resource_job = AsyncMock(side_effect=execute)
    processor = AddResourceProcessor(service, asyncio.get_running_loop())

    await processor._process(msg, msg.to_dict())

    completed = await tracker.get(msg.task_id, account_id="acme", user_id="alice")
    assert completed is not None
    assert completed.status == TaskStatus.CANCELLED
    assert not continued_after_cancel
    service.rollback_cancelled_add_resource.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_semantic_dag_stops_before_scheduling_work(monkeypatch):
    fake_fs = MagicMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        lambda: fake_fs,
    )
    processor = MagicMock()
    resource_lock = MagicMock()
    resource_lock.close = AsyncMock()
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=ctx,
        lock=resource_lock,
        is_cancelled=lambda: True,
    )

    await executor.run("viking://resources/demo")

    assert executor.stale
    fake_fs.ls.assert_not_called()
    resource_lock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_semantic_cancellation_drains_inflight_work_before_releasing_lock(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        MagicMock,
    )
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = False
    processor = MagicMock()

    async def vectorize(*_args, **_kwargs):
        started.set()
        await release.wait()

    processor._vectorize_directory = AsyncMock(side_effect=vectorize)
    resource_lock = MagicMock()
    resource_lock.close = AsyncMock()
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=ctx,
        lock=resource_lock,
        is_cancelled=lambda: cancelled,
    )
    work = DagWork(
        kind="vectorize",
        dir_uri="viking://resources/demo",
        vectorize_task=VectorizeTask(
            task_type="directory",
            uri="viking://resources/demo",
            context_type="resource",
            ctx=ctx,
        ),
    )
    executor._schedule_dir = lambda *_args, **_kwargs: executor._schedule_work(work)

    run_task = asyncio.create_task(executor.run("viking://resources/demo"))
    await started.wait()
    cancelled = True
    assert executor._stop_if_cancelled()
    await asyncio.sleep(0)

    resource_lock.close.assert_not_awaited()
    release.set()
    await run_task
    resource_lock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_semantic_cancellation_after_embedding_registration_releases_lock(monkeypatch):
    from openviking.storage.queuefs.embedding_tracker import EmbeddingTaskTracker

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        MagicMock,
    )
    tracker = EmbeddingTaskTracker.get_instance()
    tracker._tasks.clear()
    cancelled = False
    original_register = tracker.register

    async def cancel_after_register(*args, **kwargs):
        nonlocal cancelled
        await original_register(*args, **kwargs)
        cancelled = True

    monkeypatch.setattr(tracker, "register", cancel_after_register)
    resource_lock = MagicMock()
    resource_lock.close = AsyncMock()
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=MagicMock(),
        context_type="resource",
        max_concurrent_llm=1,
        ctx=ctx,
        semantic_msg_id="semantic-1",
        lock=resource_lock,
        is_cancelled=lambda: cancelled,
    )

    def seed_vectorization(*_args, **_kwargs):
        executor._vectorize_task_count = 1
        executor._pending_vectorize_tasks = [
            VectorizeTask(
                task_type="file",
                uri="viking://resources/demo/file.txt",
                context_type="resource",
                ctx=ctx,
                file_path="viking://resources/demo/file.txt",
            )
        ]
        executor._root_done.set()

    executor._schedule_dir = seed_vectorization

    await executor.run("viking://resources/demo")

    assert "semantic-1" not in tracker._tasks
    resource_lock.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_semantic_processor_restores_cancelled_state_after_restart(monkeypatch):
    agfs = _FakeAgfs()
    original_tracker = TaskTracker(store=PersistentTaskStore(agfs))
    task = await original_tracker.create(
        "add_resource",
        resource_id="viking://resources/demo",
        account_id="acme",
        user_id="alice",
        task_id="task-1",
    )
    await original_tracker.cancel(task.task_id, account_id="acme", user_id="alice")
    set_task_tracker(TaskTracker(store=PersistentTaskStore(agfs)))
    resource_lock = MagicMock()
    resource_lock.close = AsyncMock()
    lock_scope = SimpleNamespace(lock=resource_lock, close=resource_lock.close)
    dag_constructed = False

    class FakeDagExecutor:
        stale = True

        def __init__(self, **kwargs):
            nonlocal dag_constructed
            dag_constructed = True

        async def run(self, _root_uri):
            raise AssertionError("cancelled work must not enter the semantic DAG")

        def get_stats(self):
            return SimpleNamespace()

    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=False)
    fake_fs._uri_to_path.return_value = "/local/acme/resources/demo"
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=lock_scope),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        FakeDagExecutor,
    )
    processor = SemanticProcessor()
    processor._circuit_breaker.check = MagicMock()
    processor._enqueue_parent_refresh = AsyncMock()
    processor._sync_topdown_recursive = AsyncMock()
    msg = SemanticMsg(
        uri="viking://resources/demo",
        context_type="resource",
        account_id="acme",
        user_id="alice",
        role="user",
        source_task_id="task-1",
    )

    await processor.on_dequeue(msg.to_dict())

    assert not dag_constructed
    processor._circuit_breaker.check.assert_not_called()
    processor._sync_topdown_recursive.assert_not_awaited()
    resource_lock.close.assert_awaited_once()
    processor._enqueue_parent_refresh.assert_not_awaited()
