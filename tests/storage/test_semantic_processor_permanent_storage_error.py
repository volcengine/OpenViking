# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Permanent storage failures must finish semantic work without blocking the API."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import pytest

from openviking.pyagfs.exceptions import AGFSNotADirectoryError
from openviking.service.task_work_index import TaskWorkIndex
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.semantic_dag import DagStats
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.viking_fs import SyncDiff
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen


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
    dag = SimpleNamespace(run=AsyncMock(), get_stats=Mock(return_value=DagStats()), stale=False)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor", Mock(return_value=dag)
    )
    retry_queue = SimpleNamespace(enqueue=AsyncMock())
    manager = SimpleNamespace(SEMANTIC="Semantic", get_queue=lambda name: retry_queue)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    processor = SemanticProcessor()
    processor._sync_topdown_recursive = AsyncMock(return_value=SyncDiff())
    success, requeue, error = Mock(), Mock(), Mock()
    processor.set_callbacks(success, requeue, error)
    tracker = get_request_wait_tracker()
    telemetry_ids = []

    def message(**kwargs):
        msg = SemanticMsg(
            uri="viking://resources/notes.txt",
            context_type="resource",
            telemetry_id=str(uuid4()),
            propagate_to_parent=False,
            **kwargs,
        )
        telemetry_ids.append(msg.telemetry_id)
        tracker.register_request(msg.telemetry_id)
        tracker.register_semantic_root(msg.telemetry_id, msg.id)
        return msg

    yield SimpleNamespace(
        processor=processor,
        pathlock=pathlock,
        lease=lease,
        dag=dag,
        retry_queue=retry_queue,
        success=success,
        requeue=requeue,
        error=error,
        tracker=tracker,
        message=message,
    )
    for telemetry_id in telemetry_ids:
        tracker.cleanup(telemetry_id)


def _storage_error(wrapped):
    error = AGFSNotADirectoryError("timeout at /arbitrary/notes.txt")
    if wrapped:
        wrapper = RuntimeError("semantic storage operation failed")
        wrapper.__cause__ = error
        return wrapper
    return error


async def _assert_request_failed(env, msg, error):
    await asyncio.wait_for(env.tracker.wait_for_request(msg.telemetry_id), timeout=1)
    assert env.tracker.build_queue_status(msg.telemetry_id)["Semantic"] == {
        "processed": 0,
        "requeue_count": 0,
        "error_count": 1,
        "errors": [{"message": str(error)}],
    }


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

    await asyncio.wait_for(
        env.processor.on_dequeue(data, lock=None if owned else env.lease), timeout=1
    )

    operation.assert_awaited_once()
    env.error.assert_called_once_with(str(error), data)
    env.success.assert_not_called()
    env.requeue.assert_not_called()
    env.retry_queue.enqueue.assert_not_awaited()
    await _assert_request_failed(env, msg, error)
    env.processor._circuit_breaker.check()
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
    index = TaskWorkIndex()
    finalize = AsyncMock()
    index.set_callbacks(
        finalize_before_ack=finalize, is_cancellation_requested=lambda task_id: False
    )
    queue = NamedQueue(
        object(), "/queue", "Semantic", dequeue_handler=env.processor, task_work_index=index
    )
    payload = msg.to_dict()
    if task_owned:
        payload["task_id"] = "ingestion-task"
    await queue.enqueue(payload)
    queued_payload = backend.write.call_args.args[1].decode()
    backend.read = AsyncMock(
        side_effect=[json.dumps({"id": "queue-message", "data": queued_payload}).encode(), b"0"]
    )
    assert index.has_work("ingestion-task") is task_owned

    await asyncio.wait_for(queue.dequeue(), timeout=1)

    assert backend.write.await_args_list == [
        call("/queue/Semantic/enqueue", queued_payload.encode()),
        call("/queue/Semantic/ack", b"queue-message"),
    ]
    status = await queue.get_status()
    assert status.is_complete
    assert status.in_progress == status.processed == status.requeue_count == 0
    assert status.error_count == 1
    assert [entry.message for entry in status.errors] == [str(error)]
    assert not index.has_work("ingestion-task")  # Includes active consumer registration.
    assert index.failure("ingestion-task") == (str(error) if task_owned else None)
    if task_owned:
        finalize.assert_awaited_once()
        assert finalize.call_args.args[0].task_id == "ingestion-task"
    else:
        finalize.assert_not_awaited()
    env.retry_queue.enqueue.assert_not_awaited()
    env.pathlock.pathlock_release.assert_awaited_once_with(env.lease)
    await _assert_request_failed(env, msg, error)


@pytest.mark.asyncio
async def test_malformed_resources_do_not_pause_following_valid_work(semantic_env):
    env = semantic_env
    for wrapped in [False, True, False, True]:
        msg = env.message()
        error = _storage_error(wrapped)
        env.dag.run.side_effect = error
        await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)
        await _assert_request_failed(env, msg, error)

    env.dag.run.side_effect = None
    valid = env.message()
    await asyncio.wait_for(env.processor.on_dequeue(valid.to_dict()), timeout=1)
    await asyncio.wait_for(env.tracker.wait_for_request(valid.telemetry_id), timeout=1)

    assert env.error.call_count == 4
    env.success.assert_called_once_with()
    env.requeue.assert_not_called()
    env.retry_queue.enqueue.assert_not_awaited()
    assert env.dag.run.await_count == 5
    assert env.tracker.build_queue_status(valid.telemetry_id)["Semantic"]["processed"] == 1
    env.processor._circuit_breaker.check()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "trips_breaker"),
    [(TimeoutError("API timeout"), True), (LockAcquisitionError("tree is locked"), False)],
)
async def test_retryable_errors_keep_existing_behavior(
    semantic_env, monkeypatch, error, trips_breaker
):
    env = semantic_env
    env.processor._circuit_breaker = CircuitBreaker(failure_threshold=1)
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.asyncio.sleep", sleep)
    msg = env.message()
    env.dag.run.side_effect = error

    await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    env.retry_queue.enqueue.assert_awaited_once()
    assert env.retry_queue.enqueue.call_args.args[0].id == msg.id
    env.requeue.assert_called_once_with()
    env.success.assert_called_once_with()
    env.error.assert_not_called()
    assert not env.tracker.is_complete(msg.telemetry_id)
    assert env.tracker.build_queue_status(msg.telemetry_id)["Semantic"]["requeue_count"] == 1
    if trips_breaker:
        with pytest.raises(CircuitBreakerOpen):
            env.processor._circuit_breaker.check()
        sleep.assert_awaited_once()
    else:
        env.processor._circuit_breaker.check()
        sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_permanent_api_error_still_opens_breaker(semantic_env):
    env = semantic_env
    msg = env.message()
    error = RuntimeError("400 invalid model parameter")
    env.dag.run.side_effect = error

    await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    env.error.assert_called_once()
    env.success.assert_not_called()
    env.retry_queue.enqueue.assert_not_awaited()
    await _assert_request_failed(env, msg, error)
    with pytest.raises(CircuitBreakerOpen):
        env.processor._circuit_breaker.check()


@pytest.mark.asyncio
async def test_open_breaker_requeues_then_recovers(semantic_env, monkeypatch):
    env = semantic_env
    now = [100.0]
    monkeypatch.setattr("openviking.utils.circuit_breaker.time.monotonic", lambda: now[0])
    sleep = AsyncMock()
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.asyncio.sleep", sleep)
    breaker = env.processor._circuit_breaker
    breaker.record_failure(RuntimeError("400 invalid model parameter"))
    msg = env.message()

    await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    env.dag.run.assert_not_awaited()
    env.retry_queue.enqueue.assert_awaited_once()
    sleep.assert_awaited_once_with(30)
    env.requeue.assert_called_once_with()
    assert not env.tracker.is_complete(msg.telemetry_id)
    with pytest.raises(CircuitBreakerOpen):
        breaker.check()

    now[0] += 301
    await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)
    await asyncio.wait_for(env.tracker.wait_for_request(msg.telemetry_id), timeout=1)

    env.dag.run.assert_awaited_once_with(msg.uri)
    assert env.success.call_count == 2
    env.error.assert_not_called()
    assert breaker.retry_after == 0
    breaker.check()
