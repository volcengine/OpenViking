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
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_work_index import TaskWorkIndex
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_executor import SemanticTreeStats
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.viking_fs import SyncDiff
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.circuit_breaker import CircuitBreaker


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

    result = await asyncio.wait_for(
        env.processor.on_dequeue(data, lock=None if owned else env.lease), timeout=1
    )

    operation.assert_awaited_once()
    assert result.outcome is ProcessOutcome.FAILED
    assert result.error == str(error)
    env.retry_queue.enqueue.assert_not_awaited()
    await _assert_request_failed(env, msg, error)
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
    index = TaskWorkIndex()
    finalize = AsyncMock()
    index.set_callbacks(
        finalize_before_ack=finalize, is_cancellation_requested=lambda task_id: False
    )
    queue = NamedQueue(
        object(),
        "/queue",
        "Semantic",
        dequeue_handler=env.processor,
        middlewares=[TaskWorkQueueMiddleware(index)],
    )
    payload = msg.to_dict()
    if task_owned:
        payload["task_id"] = "ingestion-task"
    await queue.enqueue(payload)
    queued_payload = backend.write.call_args.args[1].decode()
    backend.read = AsyncMock(
        side_effect=[
            json.dumps({"id": "queue-message", "data": queued_payload}).encode(),
            json.dumps({"pending": 0, "processing": 0}).encode(),
        ]
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
        result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)
        assert result.outcome is ProcessOutcome.FAILED
        assert result.error == str(error)
        await _assert_request_failed(env, msg, error)

    env.dag.run.side_effect = None
    valid = env.message()
    result = await asyncio.wait_for(env.processor.on_dequeue(valid.to_dict()), timeout=1)
    assert result.outcome is ProcessOutcome.SUCCESS
    await asyncio.wait_for(env.tracker.wait_for_request(valid.telemetry_id), timeout=1)

    env.retry_queue.enqueue.assert_not_awaited()
    assert env.dag.run.await_count == 5
    assert env.tracker.build_queue_status(valid.telemetry_id)["Semantic"]["processed"] == 1
    env.processor._account_breaker(valid.account_id).check()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [TimeoutError("API timeout"), LockAcquisitionError("tree is locked")]
)
async def test_post_execution_storage_errors_do_not_requeue_or_trip_breaker(semantic_env, error):
    env = semantic_env
    breaker = CircuitBreaker(failure_threshold=1)
    msg = env.message()
    env.processor._circuit_breakers[msg.account_id] = breaker
    env.dag.run.side_effect = error

    result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.error == str(error)
    env.retry_queue.enqueue.assert_not_awaited()
    await _assert_request_failed(env, msg, error)
    breaker.check()


@pytest.mark.asyncio
async def test_permanent_api_error_does_not_open_breaker(semantic_env):
    env = semantic_env
    msg = env.message()
    error = RuntimeError("400 invalid model parameter")
    env.dag.run.side_effect = error

    result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.error == str(error)
    env.retry_queue.enqueue.assert_not_awaited()
    await _assert_request_failed(env, msg, error)
    env.processor._account_breaker(msg.account_id).check()


@pytest.mark.asyncio
async def test_open_breaker_waits_then_recovers_in_same_delivery(semantic_env):
    env = semantic_env
    msg = env.message()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=0.01)
    env.processor._circuit_breakers[msg.account_id] = breaker
    breaker.record_failure(RuntimeError("503 unavailable"))

    result = await asyncio.wait_for(env.processor.on_dequeue(msg.to_dict()), timeout=1)

    assert result.outcome is ProcessOutcome.SUCCESS
    await asyncio.wait_for(env.tracker.wait_for_request(msg.telemetry_id), timeout=1)
    env.dag.run.assert_awaited_once_with(msg.uri)
    env.retry_queue.enqueue.assert_not_awaited()
    assert breaker.retry_after == 0
    breaker.check()
