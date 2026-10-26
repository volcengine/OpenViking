# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Permanent storage failures must finish semantic work without blocking the API."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from openviking.models.model_error import ModelError, ModelErrorKind
from openviking.pyagfs.exceptions import AGFSNotADirectoryError
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.service.task_work_index import bind_task_context
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_executor import SemanticTreeStats
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.viking_fs import SyncDiff
from openviking.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from tests.test_task_tracker import _FakeAgfs


@pytest.fixture
def semantic_env(monkeypatch):
    lease = {"lease_ref": "semantic-lease", "owner_id": "consumer", "owned": True}
    pathlock = SimpleNamespace(
        pathlock_adopt=AsyncMock(return_value=lease),
        pathlock_as_borrowed=AsyncMock(return_value={**lease, "owned": False}),
        pathlock_release=AsyncMock(),
    )
    fs = SimpleNamespace(_async_agfs=pathlock, exists=AsyncMock(return_value=True))
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fs)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_lock.get_viking_fs", lambda: fs)
    dag = SimpleNamespace(
        run=AsyncMock(), get_stats=Mock(return_value=SemanticTreeStats()), stale=False
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticTreeExecutor",
        Mock(return_value=dag),
    )
    retry_queue = SimpleNamespace(enqueue=AsyncMock())
    manager = SimpleNamespace(SEMANTIC="Semantic", get_queue=lambda name: retry_queue)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace()))
    processor = SemanticProcessor(vlm_resolver=resolver)
    processor._sync_topdown_recursive = AsyncMock(return_value=SyncDiff())

    def message(**kwargs):
        return SemanticMsg(
            uri="viking://resources/notes.txt",
            context_type="resource",
            telemetry_id=str(uuid4()),
            propagate_to_parent=False,
            **kwargs,
        )

    yield SimpleNamespace(
        processor=processor,
        pathlock=pathlock,
        lease=lease,
        dag=dag,
        retry_queue=retry_queue,
        message=message,
    )


def _storage_error(wrapped):
    error = AGFSNotADirectoryError("timeout at /arbitrary/notes.txt")
    if wrapped:
        wrapper = RuntimeError("semantic storage operation failed")
        wrapper.__cause__ = error
        return wrapper
    return error


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("boundary", ["sync", "dag"])
@pytest.mark.parametrize("owned", [False, True])
async def test_storage_error_finishes_request_and_respects_lock_ownership(
    semantic_env, wrapped, boundary, owned
):
    env = semantic_env
    msg = env.message(
        target_uri="viking://resources/existing.txt" if boundary == "sync" else "",
        lock_handoff={"owner_id": "producer"} if owned else None,
    )
    error = _storage_error(wrapped)
    operation = env.processor._sync_topdown_recursive if boundary == "sync" else env.dag.run
    operation.side_effect = error
    data = msg.to_dict()

    result = await asyncio.wait_for(
        env.processor.on_dequeue(data, lock=None if owned else env.lease), timeout=1
    )

    assert result.outcome is ProcessOutcome.FAILED
    assert result.error == str(error)
    operation.assert_awaited_once()
    env.retry_queue.enqueue.assert_not_awaited()
    env.processor._account_breaker(msg.account_id).check()
    if owned:
        env.pathlock.pathlock_adopt.assert_awaited_once_with(msg.lock_handoff)
        env.pathlock.pathlock_release.assert_awaited_once_with(env.lease)
    else:
        env.pathlock.pathlock_as_borrowed.assert_awaited_once_with(env.lease)
        env.pathlock.pathlock_release.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("task_owned", [False, True])
async def test_queue_acks_failed_semantic_work(semantic_env, monkeypatch, wrapped, task_owned):
    env = semantic_env
    msg = env.message(lock_handoff={"owner_id": "producer"})
    error = _storage_error(wrapped)
    env.dag.run.side_effect = error
    backend = SimpleNamespace(mkdir=AsyncMock(), write=AsyncMock(return_value="queue-message"))
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda agfs: backend
    )
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    queue = NamedQueue(
        object(),
        "/queue",
        "Semantic",
        dequeue_handler=env.processor,
        middlewares=[TaskWorkQueueMiddleware()],
    )
    payload = msg.to_dict()
    if task_owned:
        payload["task_id"] = "ingestion-task"
        task = await tracker.create(
            "add_resource",
            task_id="ingestion-task",
            account_id=msg.account_id,
            user_id=msg.user_id,
        )
        await tracker.start(
            task.task_id,
            account_id=msg.account_id,
            user_id=msg.user_id,
        )
        with bind_task_context(task.task_id, msg.account_id, msg.user_id):
            await queue.enqueue(payload)
    else:
        await queue.enqueue(payload)
    queued_payload = backend.write.call_args.args[1].decode()
    backend.read = AsyncMock(
        side_effect=[
            json.dumps({"id": "queue-message", "data": queued_payload}).encode(),
            json.dumps({"pending": 0, "processing": 0}).encode(),
        ]
    )
    assert (
        await tracker.has_work(
            "ingestion-task",
            account_id=msg.account_id,
            user_id=msg.user_id,
        )
        is task_owned
    )

    await asyncio.wait_for(queue.dequeue(), timeout=1)

    status = await queue.get_status()
    assert status.is_complete
    assert status.in_progress == status.processed == status.requeue_count == 0
    assert status.error_count == 1
    assert [entry.message for entry in status.errors] == [str(error)]
    assert not await tracker.has_work(
        "ingestion-task",
        account_id=msg.account_id,
        user_id=msg.user_id,
    )
    if task_owned:
        failed = await tracker.get(
            "ingestion-task",
            account_id=msg.account_id,
            user_id=msg.user_id,
        )
        assert failed is not None
        assert failed.status is TaskStatus.FAILED
        assert failed.error == str(error)
    env.retry_queue.enqueue.assert_not_awaited()
    env.pathlock.pathlock_release.assert_awaited_once_with(env.lease)


@pytest.mark.asyncio
async def test_malformed_resources_do_not_pause_following_valid_work(semantic_env):
    env = semantic_env
    for wrapped in [False, True, False, True]:
        msg = env.message()
        error = _storage_error(wrapped)
        env.dag.run.side_effect = error
        result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)
        assert result.outcome is ProcessOutcome.FAILED
        assert result.error == str(error)

    env.dag.run.side_effect = None
    valid = env.message()
    result = await asyncio.wait_for(env.processor.on_dequeue(valid.to_dict()), timeout=1)
    assert result.outcome is ProcessOutcome.SUCCESS

    env.retry_queue.enqueue.assert_not_awaited()
    assert env.dag.run.await_count == 5
    env.processor._account_breaker(valid.account_id).check()


@pytest.mark.asyncio
async def test_lock_error_requeues_without_consuming_failure_budget(semantic_env, monkeypatch):
    env = semantic_env
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.asyncio.sleep", sleep)
    msg = env.message()
    breaker = CircuitBreaker(failure_threshold=1)
    env.processor._circuit_breakers[msg.account_id] = breaker
    env.dag.run.side_effect = LockAcquisitionError("tree is locked")

    result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    assert result.outcome is ProcessOutcome.REQUEUED
    env.retry_queue.enqueue.assert_awaited_once()
    requeued = env.retry_queue.enqueue.call_args.args[0]
    assert requeued.id == msg.id
    assert requeued.failure_requeue_count == 0
    breaker.check()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_semantic_error_is_terminal_without_tripping_breaker(semantic_env):
    env = semantic_env
    msg = env.message()
    error = RuntimeError("unexpected semantic invariant violation")
    env.dag.run.side_effect = error

    result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.error == str(error)
    env.retry_queue.enqueue.assert_not_awaited()
    env.processor._account_breaker(msg.account_id).check()


@pytest.mark.asyncio
async def test_semantic_model_error_fails_without_requeue(semantic_env):
    env = semantic_env
    msg = env.message()
    error = ModelError(ModelErrorKind.TRANSIENT_SERVICE_ERROR, "model request failed")
    env.dag.run.side_effect = error

    result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.error == str(error)
    env.retry_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_request_does_not_open_breaker(semantic_env):
    env = semantic_env
    msg = env.message()
    error = ModelError(ModelErrorKind.INVALID_REQUEST, "400 invalid model parameter")
    env.dag.run.side_effect = error

    result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.error == str(error)
    env.retry_queue.enqueue.assert_not_awaited()
    env.processor._account_breaker(msg.account_id).check()


@pytest.mark.asyncio
async def test_open_breaker_requeues_then_recovers(semantic_env, monkeypatch):
    env = semantic_env
    now = [100.0]
    monkeypatch.setattr("openviking.utils.circuit_breaker.time.monotonic", lambda: now[0])
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.asyncio.sleep", sleep)
    msg = env.message()
    breaker = env.processor._account_breaker(msg.account_id)
    breaker.record_failure(RuntimeError("400 invalid model parameter"))

    result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    assert result.outcome is ProcessOutcome.REQUEUED
    env.dag.run.assert_not_awaited()
    env.retry_queue.enqueue.assert_awaited_once()
    sleep.assert_awaited_once_with(30)
    with pytest.raises(CircuitBreakerOpen):
        breaker.check()

    now[0] += 301
    result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)
    assert result.outcome is ProcessOutcome.SUCCESS

    env.dag.run.assert_awaited_once_with(msg.uri)
    assert breaker.retry_after == 0
    breaker.check()
