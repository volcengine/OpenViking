# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task Store behavior through the QueueFS middleware boundary."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.service.task_domain import TaskStatus, WorkState
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.service.task_work_index import (
    TaskWorkRejected,
    bind_task_context,
    extract_task_metadata,
    get_task_context,
)
from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult
from openviking.storage.queuefs.queue_middleware import ProcessContext
from openviking.telemetry import OperationTelemetry, bind_telemetry
from openviking.utils.log_correlation import log_correlation
from tests.test_task_tracker import _FakeAgfs

OWNER = {"account_id": "account", "user_id": "user"}


@pytest.fixture
async def tracked_queue(monkeypatch):
    transport = AsyncMock(spec=AsyncAGFSClient)
    transport.write.return_value = "message-1"
    transport.read.return_value = b"0"

    async def read(path):
        if path.endswith("/status"):
            return b'{"pending":0,"processing":0}'
        return transport.read.return_value

    transport.read.side_effect = read
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda _client: transport
    )
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    queue = NamedQueue(
        object(),
        "/queue",
        "Embedding",
        middlewares=[TaskWorkQueueMiddleware()],
    )
    try:
        yield queue, tracker, transport
    finally:
        set_task_tracker(None)


async def create_task(tracker: TaskTracker, task_id: str = "task-1") -> None:
    task = await tracker.get(task_id, **OWNER)
    if task is None:
        task = await tracker.create("add_resource", task_id=task_id, **OWNER)
        await tracker.start(task.task_id, **OWNER)


async def enqueue_task(queue, tracker, transport, task_id="task-1"):
    await create_task(tracker, task_id)
    with bind_task_context(task_id, "account", "user"):
        await queue.enqueue({"value": 1})
    return {"id": "message-1", "data": transport.write.await_args.args[1].decode()}


def test_log_correlation_uses_bound_task_and_telemetry() -> None:
    telemetry = OperationTelemetry(operation="add_resource_job", enabled=True)

    with bind_task_context("task-123", "account", "user"), bind_telemetry(telemetry):
        assert log_correlation(message_id="semantic-456") == (
            f"task_id=task-123 telemetry_id={telemetry.telemetry_id} message_id=semantic-456"
        )


def test_log_correlation_accepts_explicit_queue_ids_without_context() -> None:
    assert (
        log_correlation(task_id="task-1", telemetry_id="tm-1", message_id="embedding-2")
        == "task_id=task-1 telemetry_id=tm-1 message_id=embedding-2"
    )


async def test_enqueue_keeps_content_owner_separate_from_task_owner(tracked_queue):
    queue, tracker, transport = tracked_queue
    task_owner = {"account_id": "account", "user_id": "task-owner"}
    task = await tracker.create("add_resource", task_id="task-1", **task_owner)
    await tracker.start(task.task_id, **task_owner)
    payload = {
        "task_id": "task-1",
        "account_id": "account",
        "user_id": "content-owner",
        "task_account_id": "business-account",
        "task_user_id": "business-user",
        "payload": {
            "context_data": {
                "account_id": "account",
                "owner_user_id": "content-owner",
            }
        },
    }

    with bind_task_context("task-1", "account", "task-owner"):
        await queue.enqueue(payload)

    persisted = json.loads(transport.write.await_args.args[1])
    assert persisted["user_id"] == "content-owner"
    assert persisted["task_account_id"] == "business-account"
    assert persisted["task_user_id"] == "business-user"
    assert persisted["payload"]["context_data"]["owner_user_id"] == "content-owner"
    assert persisted["_task_account_id"] == "account"
    assert persisted["_task_user_id"] == "task-owner"
    assert extract_task_metadata(persisted).user_id == "task-owner"
    metadata = extract_task_metadata(persisted)
    assert (
        await tracker.get_work_state(
            metadata.task_id,
            metadata.work_id,
            **task_owner,
        )
        is WorkState.PENDING
    )


async def test_enqueue_does_not_stamp_task_owner_without_authoritative_context(tracked_queue):
    queue, _, transport = tracked_queue
    transport.write.reset_mock()

    with bind_task_context("task-1", "", ""):
        with pytest.raises(TaskWorkRejected, match="missing owner metadata"):
            await queue.enqueue(
                {
                    "task_id": "task-1",
                    "account_id": "account",
                    "user_id": "content-owner",
                }
            )

    transport.write.assert_not_awaited()


async def test_enqueue_failure_records_failed_work(tracked_queue):
    queue, tracker, transport = tracked_queue
    await create_task(tracker)
    transport.write.side_effect = OSError("write failed")
    enqueued_work = []

    with (
        bind_task_context("task-1", "account", "user", enqueued_work=enqueued_work),
        pytest.raises(OSError, match="write failed"),
    ):
        await queue.enqueue({})

    payload = json.loads(transport.write.await_args.args[1])
    assert enqueued_work == [("Embedding", payload["_task_work_id"])]
    status = await tracker.build_queue_status("task-1", **OWNER)
    assert status["Embedding"]["error_count"] == 1
    task = await tracker.get("task-1", **OWNER)
    assert task is not None and task.status is TaskStatus.FAILED
    assert task.error == "write failed"


@pytest.mark.parametrize("state", [WorkState.PENDING, WorkState.RUNNING])
@pytest.mark.parametrize("failure", [OSError("write failed"), asyncio.CancelledError()])
async def test_failed_replacement_enqueue_preserves_existing_work(tracked_queue, state, failure):
    queue, tracker, transport = tracked_queue
    message = await enqueue_task(queue, tracker, transport)
    metadata = extract_task_metadata(message)
    if state is WorkState.RUNNING:
        await tracker.start_work("task-1", metadata.work_id, **OWNER)
    transport.write.side_effect = failure
    enqueued_work = []

    with (
        bind_task_context("task-1", "account", "user", enqueued_work=enqueued_work),
        pytest.raises(type(failure)),
    ):
        await queue.enqueue(json.loads(message["data"]))

    assert await tracker.get_work_state("task-1", metadata.work_id, **OWNER) is state
    assert (await tracker.get("task-1", **OWNER)).status is TaskStatus.RUNNING
    assert enqueued_work == []

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            await tracker.complete("task-1", {"recovered": True}, **OWNER)
            return ProcessResult.success({"recovered": True})

    queue.set_dequeue_handler(Handler())
    result = await queue.process_dequeued(message)
    assert result.value == {"recovered": True}
    assert await tracker.get_work_state("task-1", metadata.work_id, **OWNER) is WorkState.DONE
    assert (await tracker.get("task-1", **OWNER)).status is TaskStatus.COMPLETED


async def test_cancelled_enqueue_discards_uncommitted_work(tracked_queue):
    queue, tracker, transport = tracked_queue
    await create_task(tracker)
    transport.write.side_effect = asyncio.CancelledError()

    with bind_task_context("task-1", "account", "user"):
        with pytest.raises(asyncio.CancelledError):
            await queue.enqueue({})

    assert not await tracker.has_work("task-1", **OWNER)
    task = await tracker.get("task-1", **OWNER)
    assert task is not None and task.status is TaskStatus.FAILED
    assert task.error == "Task operation interrupted before its result was persisted"


async def test_successful_process_records_context_and_vector_count(tracked_queue):
    queue, tracker, transport = tracked_queue
    message = await enqueue_task(queue, tracker, transport)
    metadata = extract_task_metadata(message)
    seen = []

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            seen.append(get_task_context())
            return ProcessResult.success({"vector_written": True})

    await tracker.complete("task-1", {"done": True}, **OWNER)
    queue.set_dequeue_handler(Handler())
    result = await queue.process_dequeued(message)

    assert result.outcome is ProcessOutcome.SUCCESS
    assert seen[0].task_id == "task-1"
    assert seen[0].work_id == metadata.work_id
    assert get_task_context() is None
    assert await tracker.get_context_count("task-1", **OWNER) == 1
    task = await tracker.get("task-1", **OWNER)
    assert task is not None and task.status is TaskStatus.COMPLETED
    await queue.process_dequeued(message)
    assert len(seen) == 1
    assert await tracker.get_context_count("task-1", **OWNER) == 1
    assert (await tracker.build_queue_status("task-1", **OWNER))["Embedding"]["processed"] == 1


async def test_requeue_keeps_work_open_for_redelivery(tracked_queue):
    queue, tracker, transport = tracked_queue
    message = await enqueue_task(queue, tracker, transport)
    outcomes = [
        ProcessResult.requeued(),
        ProcessResult.failed("combined", errors=["first", "second"]),
    ]

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            return outcomes.pop(0)

    queue.set_dequeue_handler(Handler())
    first = await queue.process_dequeued(message)
    metadata = extract_task_metadata(message)
    assert first.outcome is ProcessOutcome.REQUEUED
    assert await tracker.get_work_state("task-1", metadata.work_id, **OWNER) is WorkState.PENDING
    assert await tracker.has_work("task-1", **OWNER)

    await tracker.complete("task-1", {"done": True}, **OWNER)
    second = await queue.process_dequeued(message)
    assert second.outcome is ProcessOutcome.FAILED
    assert not await tracker.has_work("task-1", **OWNER)
    task = await tracker.get("task-1", **OWNER)
    assert task.status is TaskStatus.FAILED
    assert task.error == "combined"
    assert task.result["queue_status"]["Embedding"] == {
        "processed": 0,
        "requeue_count": 1,
        "error_count": 2,
        "errors": [{"message": "first"}, {"message": "second"}],
    }


async def test_terminal_task_settles_running_redelivery_without_handler(monkeypatch):
    calls = []
    settled = []
    message = {
        "id": "message-1",
        "data": json.dumps(
            {
                "task_id": "task-1",
                "_task_work_id": "work-1",
                "_task_account_id": "account",
                "_task_user_id": "user",
            }
        ),
    }

    class FakeTracker:
        async def get_work_state(self, task_id, work_id, *, account_id, user_id):
            assert (task_id, work_id, account_id, user_id) == (
                "task-1",
                "work-1",
                "account",
                "user",
            )
            return WorkState.RUNNING

        async def get_task_status(self, task_id, *, account_id, user_id):
            assert (task_id, account_id, user_id) == ("task-1", "account", "user")
            return TaskStatus.COMPLETED

        async def settle_work(self, task_id, work_id, *, account_id, user_id):
            settled.append((task_id, work_id, account_id, user_id))

        async def reconcile_delivery(self, *args, **kwargs):
            pytest.fail("terminal task delivery must not be reconciled")

        async def start_work(self, *args, **kwargs):
            pytest.fail("terminal task delivery must not be restarted")

        async def register_running_task(self, *args, **kwargs):
            pytest.fail("terminal task delivery must not register running work")

    monkeypatch.setattr(
        TaskWorkQueueMiddleware,
        "_tracker",
        property(lambda _self: FakeTracker()),
    )

    middleware = TaskWorkQueueMiddleware()

    async def call_next(ctx):
        calls.append(ctx.message)
        return ProcessResult.success()

    result = await middleware.process(
        ProcessContext("Embedding", message, cancel=AsyncMock()),
        call_next,
    )

    assert result.outcome is ProcessOutcome.SUCCESS
    assert calls == []
    assert settled == [("task-1", "work-1", "account", "user")]


async def test_clear_settles_committed_work(tracked_queue):
    queue, tracker, transport = tracked_queue
    message = await enqueue_task(queue, tracker, transport)
    transport.read.return_value = json.dumps([message]).encode()
    transport.write.reset_mock()
    transport.write.return_value = ""

    assert await queue.clear() is True

    transport.write.assert_awaited_once_with("/queue/Embedding/clear", b"")
    assert not await tracker.has_work("task-1", **OWNER)


async def test_cancelled_task_uses_handler_cancellation_path(tracked_queue):
    queue, tracker, transport = tracked_queue
    message = await enqueue_task(queue, tracker, transport)
    calls = []

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            pytest.fail("cancelled task reached handler")

        async def on_cancelled(self, data):
            calls.append(data)
            return ProcessResult.cancelled()

    queue.set_dequeue_handler(Handler())
    await tracker.cancel("task-1", **OWNER)
    result = await queue.process_dequeued(message)

    assert result.outcome is ProcessOutcome.CANCELLED
    assert calls == [message]
    task = await tracker.get("task-1", **OWNER)
    assert task is not None and task.status is TaskStatus.CANCELLED
