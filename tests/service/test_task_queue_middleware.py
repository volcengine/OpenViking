# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task Store behavior through the QueueFS middleware boundary."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock, create_autospec

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.service.task_domain import TaskStatus, WorkState
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_store import (
    PersistentTaskStore,
    StoreOperationCode,
    StoreOperationResult,
)
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.service.task_work_index import (
    TaskWorkRejected,
    bind_task_context,
    extract_task_metadata,
    get_task_context,
)
from openviking.storage.errors import HandoffRecoveryBlocked
from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
from openviking.storage.queuefs.process_result import (
    ProcessOutcome,
    ProcessResult,
)
from openviking.storage.queuefs.queue_manager import QueueManager
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


@pytest.mark.parametrize("queue_name", ["Embedding", "ExternalTask"])
async def test_work_save_failure_leaves_delivery_unacknowledged(
    tracked_queue, monkeypatch, queue_name
):
    queue, tracker, transport = tracked_queue
    queue.name = queue_name
    message = await enqueue_task(queue, tracker, transport)
    transport.read.return_value = json.dumps(message).encode()
    monkeypatch.setattr(
        tracker, "mark_work_done", AsyncMock(side_effect=OSError("store unavailable"))
    )
    handler = AsyncMock(spec=DequeueHandlerBase)
    handler.on_dequeue.return_value = ProcessResult.success()
    queue.set_dequeue_handler(handler)
    transport.write.reset_mock()
    await queue.dequeue()
    handler.on_dequeue.assert_awaited_once()
    transport.write.assert_not_awaited()
    assert await tracker.has_work("task-1", **OWNER)
    assert (await tracker.get("task-1", **OWNER)).status is TaskStatus.RUNNING


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


@pytest.mark.parametrize("failure", ["none", "work", "backend"])
async def test_native_retry_persists_work_before_returning_original_delivery(
    tracked_queue, monkeypatch, failure
):
    queue, tracker, transport = tracked_queue
    message = await enqueue_task(queue, tracker, transport)
    metadata = extract_task_metadata(message)
    assert metadata is not None
    handler = AsyncMock(spec=DequeueHandlerBase)
    handler.on_dequeue.return_value = ProcessResult.retry()
    queue.set_dequeue_handler(handler)
    transport.read.return_value = json.dumps(message).encode()
    transport.write.reset_mock()
    if failure == "work":
        monkeypatch.setattr(
            tracker, "mark_work_requeued", AsyncMock(side_effect=OSError("work save unavailable"))
        )
    else:

        async def write(path, data):
            assert path == "/queue/Embedding/retry"
            assert data == b"message-1"
            assert (
                await tracker.get_work_state("task-1", metadata.work_id, **OWNER)
                is WorkState.PENDING
            )
            if failure == "backend":
                raise OSError("backend unavailable")
            return 0

        transport.write.side_effect = write
    await queue.dequeue()
    if failure == "work":
        transport.write.assert_not_awaited()
    else:
        transport.write.assert_awaited_once_with("/queue/Embedding/retry", b"message-1")
    assert await tracker.has_work("task-1", **OWNER)


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
        async def start_work(self, task_id, work_id, *, queue_name, account_id, user_id):
            assert (task_id, work_id, account_id, user_id) == (
                "task-1",
                "work-1",
                "account",
                "user",
            )
            assert queue_name == "Embedding"
            return StoreOperationResult.rejected(StoreOperationCode.ALREADY_TERMINAL)

        async def settle_work(self, task_id, work_id, *, account_id, user_id):
            settled.append((task_id, work_id, account_id, user_id))

        async def reconcile_delivery(self, *args, **kwargs):
            pytest.fail("terminal task delivery must not be reconciled")

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
    metadata = extract_task_metadata(message)
    await tracker.start_work("task-1", metadata.work_id, **OWNER)
    calls = []

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            pytest.fail("cancelled task reached handler")

        async def on_cancelled(self, data):
            calls.append(data)
            return ProcessResult.cancelled()

    queue.set_dequeue_handler(Handler())
    await tracker.cancel("task-1", **OWNER)
    assert (await tracker.get("task-1", **OWNER)).status is TaskStatus.CANCELLING
    result = await queue.process_dequeued(message)

    assert result.outcome is ProcessOutcome.CANCELLED
    assert calls == [message]
    task = await tracker.get("task-1", **OWNER)
    assert task is not None and task.status is TaskStatus.CANCELLED
    assert await tracker.get_work_state("task-1", metadata.work_id, **OWNER) is WorkState.CANCELLED


async def test_cancellation_between_precheck_and_start_rejects_handler(tracked_queue, monkeypatch):
    queue, tracker, transport = tracked_queue
    message = await enqueue_task(queue, tracker, transport)
    start = tracker.start_work
    cleanup = []

    async def cancel_before_start(*args, **kwargs):
        await tracker.cancel("task-1", **OWNER)
        return await start(*args, **kwargs)

    monkeypatch.setattr(tracker, "start_work", cancel_before_start)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            pytest.fail("rejected start reached business handler")

        async def on_cancelled(self, data):
            cleanup.append(data)
            return ProcessResult.cancelled()

    queue.set_dequeue_handler(Handler())
    result = await queue.process_dequeued(message)
    assert result.outcome is ProcessOutcome.CANCELLED
    assert cleanup == [message]
    assert (await tracker.get("task-1", **OWNER)).status is TaskStatus.CANCELLED


@pytest.mark.parametrize("queue_name", ["Embedding", "ExternalTask"])
async def test_running_cancellation_waits_for_handler_cleanup(tracked_queue, queue_name):
    queue, tracker, transport = tracked_queue
    queue.name = queue_name
    message = await enqueue_task(queue, tracker, transport)
    metadata = extract_task_metadata(message)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

    queue.set_dequeue_handler(Handler())
    executing = asyncio.create_task(queue.process_dequeued(message))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await tracker.cancel("task-1", **OWNER)
        await asyncio.wait_for(cleaning.wait(), 1)
        assert (await tracker.get("task-1", **OWNER)).status is TaskStatus.CANCELLING
        assert (
            await tracker.get_work_state("task-1", metadata.work_id, **OWNER) is WorkState.RUNNING
        )
        release.set()
        assert (await asyncio.wait_for(executing, 1)).outcome is ProcessOutcome.CANCELLED
        assert (await tracker.get("task-1", **OWNER)).status is TaskStatus.CANCELLED
        assert (
            await tracker.get_work_state("task-1", metadata.work_id, **OWNER) is WorkState.CANCELLED
        )
    finally:
        release.set()
        executing.cancel()
        await asyncio.gather(executing, return_exceptions=True)


async def test_normal_process_uses_one_admission_and_one_cancellation_read(
    tracked_queue, monkeypatch
):
    queue, tracker, transport = tracked_queue
    message = await enqueue_task(queue, tracker, transport)
    await tracker.complete("task-1", {"done": True}, **OWNER)
    store = tracker._store
    start = create_autospec(store.start_work, side_effect=store.start_work)
    status = create_autospec(store.get_task_status, side_effect=store.get_task_status)
    work = create_autospec(store.get_work, side_effect=store.get_work)
    finish = create_autospec(store.finish_work, side_effect=store.finish_work)
    monkeypatch.setattr(store, "start_work", start)
    monkeypatch.setattr(store, "get_task_status", status)
    monkeypatch.setattr(store, "get_work", work)
    monkeypatch.setattr(store, "finish_work", finish)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            return ProcessResult.success()

    queue.set_dequeue_handler(Handler())
    assert (await queue.process_dequeued(message)).outcome is ProcessOutcome.SUCCESS
    start.assert_awaited_once_with(
        "task-1", extract_task_metadata(message).work_id, queue_name="Embedding", **OWNER
    )
    status.assert_awaited_once_with("task-1", **OWNER)
    work.assert_not_awaited()
    assert finish.await_count == 1


@pytest.mark.parametrize("worker", ["serial", "concurrent"])
@pytest.mark.parametrize("failure", ["business", "handoff", "replacement", "persist", "shutdown"])
@pytest.mark.parametrize("queue_name", ["Embedding", "ExternalTask"])
async def test_worker_exception_ack_policy(tracked_queue, monkeypatch, worker, failure, queue_name):
    queue, tracker, transport = tracked_queue
    queue.name = queue_name
    message = await enqueue_task(queue, tracker, transport)
    metadata = extract_task_metadata(message)
    await tracker.complete("task-1", {"done": True}, **OWNER)
    stop = threading.Event()
    delivered = False

    async def read(path):
        nonlocal delivered
        if path.endswith("/dequeue") and not delivered:
            delivered = True
            return json.dumps(message).encode()
        return b"{}"

    transport.read.side_effect = read
    transport.write.reset_mock()
    if failure == "replacement":

        async def fail_replacement(path, data):
            if path.endswith("/enqueue"):
                raise OSError("replacement unavailable")
            return "message-1"

        transport.write.side_effect = fail_replacement
    if failure == "persist":
        write = tracker._store._agfs.write

        async def fail_outcome(path, data, *args, **kwargs):
            if json.loads(data)["status"] == "completed":
                raise OSError("task outcome unavailable")
            return await write(path, data, *args, **kwargs)

        monkeypatch.setattr(tracker._store._agfs, "write", fail_outcome)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            stop.set()
            if failure == "business":
                raise ValueError("invalid business input")
            if failure == "handoff":
                raise HandoffRecoveryBlocked("coverage busy")
            if failure == "replacement":
                await queue.enqueue(json.loads(message["data"]))
            if failure == "shutdown":
                raise asyncio.CancelledError()
            return ProcessResult.success()

    queue.set_dequeue_handler(Handler())
    if worker == "serial":
        if failure == "shutdown":
            with pytest.raises(asyncio.CancelledError):
                await queue.dequeue()
        else:
            await queue.dequeue()
    else:
        manager = QueueManager(object())
        manager._poll_interval = 0.001
        manager._REQUEUE_POLL_INTERVAL = 0.001
        await asyncio.wait_for(manager._worker_async_concurrent(queue, stop, 2), 1)

    acks = [call for call in transport.write.await_args_list if call.args[0].endswith("/ack")]
    task = await tracker.get("task-1", **OWNER)
    assert acks == []
    assert task.status is TaskStatus.RUNNING
    assert task.error is None
    if failure in ("business", "handoff", "replacement"):
        assert (
            await tracker.get_work_state("task-1", metadata.work_id, **OWNER) is WorkState.RUNNING
        )
    else:
        assert (
            await tracker.get_work_state("task-1", metadata.work_id, **OWNER) is WorkState.PENDING
        )
    summary = await tracker.build_queue_status("task-1", **OWNER)
    assert summary["Embedding"]["error_count"] == 0
    assert summary["Embedding"]["requeue_count"] == 0
    if failure == "persist":
        monkeypatch.setattr(tracker._store._agfs, "write", write)
    transport.write.side_effect = None

    class RetryHandler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            return ProcessResult.success()

    queue.set_dequeue_handler(RetryHandler())
    assert (await queue.process_dequeued(message)).outcome is ProcessOutcome.SUCCESS
    assert (await tracker.get("task-1", **OWNER)).status is TaskStatus.COMPLETED


async def test_recovered_delivery_is_reconstructed_and_started_atomically(tracked_queue):
    queue, tracker, _transport = tracked_queue
    await tracker.create("add_resource", task_id="recovered", **OWNER)
    message = {
        "id": "recovered-message",
        "data": json.dumps(
            {
                "task_id": "recovered",
                "_task_work_id": "recovered-work",
                "_task_account_id": "account",
                "_task_user_id": "user",
            }
        ),
    }

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            assert (
                await tracker.get_work_state("recovered", "recovered-work", **OWNER)
                is WorkState.RUNNING
            )
            assert (await tracker.get("recovered", **OWNER)).status is TaskStatus.RUNNING
            await tracker.complete("recovered", {"done": True}, **OWNER)
            return ProcessResult.success()

    queue.set_dequeue_handler(Handler())
    await queue.process_dequeued(message)
    assert (await tracker.get("recovered", **OWNER)).status is TaskStatus.COMPLETED
