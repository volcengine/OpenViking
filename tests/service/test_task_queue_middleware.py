# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task Store behavior through the QueueFS middleware boundary."""

import asyncio
import json
import struct
import threading
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from openviking.pyagfs import AGFSSyncClientProtocol, AsyncAGFSClient
from openviking.service.task_domain import TaskStatus, WorkState
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_store import PersistentTaskStore, WorkRegistration
from openviking.service.task_store_sqlite import SQLiteTaskWorkStore
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
from openviking.telemetry import OperationTelemetry, bind_telemetry
from openviking.utils.log_correlation import log_correlation
from tests.test_task_tracker import _FakeAgfs

OWNER = {"account_id": "account", "user_id": "user"}


@pytest.fixture
def file_task_tracker():
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    try:
        yield tracker
    finally:
        set_task_tracker(None)


@pytest.fixture
async def tracked_queue(monkeypatch, file_task_tracker):
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
    queue = NamedQueue(
        object(),
        "/queue",
        "Embedding",
        middlewares=[TaskWorkQueueMiddleware()],
    )
    return queue, file_task_tracker, transport


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


async def test_rejected_registration_does_not_write_or_clean_up(tracked_queue, monkeypatch):
    queue, tracker, transport = tracked_queue
    discard_work = AsyncMock(wraps=tracker.discard_work)
    mark_work_failed = AsyncMock(wraps=tracker.mark_work_failed)
    monkeypatch.setattr(tracker, "discard_work", discard_work)
    monkeypatch.setattr(tracker, "mark_work_failed", mark_work_failed)
    enqueued_work: list[tuple[str, str]] = []

    with (
        bind_task_context("missing", "account", "user", enqueued_work=enqueued_work),
        pytest.raises(TaskWorkRejected, match="Task missing is unavailable"),
    ):
        await queue.enqueue({})

    transport.write.assert_not_awaited()
    discard_work.assert_not_awaited()
    mark_work_failed.assert_not_awaited()
    assert enqueued_work == []


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


async def test_cancellation_after_sqlite_registration_discards_work(tmp_path, monkeypatch):
    params: dict[str, object] = {"path": str(tmp_path / "tasks.db")}
    tracker = TaskTracker(SQLiteTaskWorkStore(params=params))
    client = Mock(spec=AGFSSyncClientProtocol)
    queue = NamedQueue(client, "/queue", "Embedding", middlewares=[TaskWorkQueueMiddleware()])
    registered, release = asyncio.Event(), asyncio.Event()
    register_work = tracker.register_work
    work_id = "registered-work"

    async def pause_after_registration(*args, **kwargs):
        registration = await register_work(*args, **kwargs)
        assert registration is WorkRegistration.CREATED
        registered.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return registration

    monkeypatch.setattr(tracker, "register_work", pause_after_registration)
    set_task_tracker(tracker)
    try:
        await tracker.load()
        await create_task(tracker)
        with bind_task_context("task-1", "account", "user"):
            enqueue = asyncio.create_task(queue.enqueue({"_task_work_id": work_id}))
            try:
                await asyncio.wait_for(registered.wait(), timeout=5)
                assert await tracker.get_work_state("task-1", work_id, **OWNER) is WorkState.PENDING
                await tracker.complete("task-1", {"done": True}, **OWNER)
                task = await tracker.get("task-1", **OWNER)
                assert task is not None and task.status is TaskStatus.RUNNING
                enqueue.cancel()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(enqueue, timeout=5)
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(enqueue, return_exceptions=True), timeout=5)

        client.write.assert_not_called()
        assert await tracker.get_work_state("task-1", work_id, **OWNER) is None
        task = await tracker.get("task-1", **OWNER)
        assert task is not None and task.status is TaskStatus.COMPLETED
    finally:
        set_task_tracker(None)
        await tracker.close()

    reopened = TaskTracker(SQLiteTaskWorkStore(params=params))
    try:
        await reopened.load()
        assert await reopened.get_work_state("task-1", work_id, **OWNER) is None
        task = await reopened.get("task-1", **OWNER)
        assert task is not None and task.status is TaskStatus.COMPLETED
    finally:
        await reopened.close()


@pytest.mark.parametrize(
    ("committed", "serial_key"),
    [(False, None), (True, None), (True, "resource-1")],
    ids=["write-failed", "plain-committed", "keyed-committed"],
)
async def test_cancellation_during_sync_enqueue(file_task_tracker, committed, serial_key):
    tracker = file_task_tracker
    client = Mock(spec=AGFSSyncClientProtocol)
    queue = NamedQueue(client, "/queue", "Embedding", middlewares=[TaskWorkQueueMiddleware()])
    await create_task(tracker)
    work_id = "writing-work"
    if not committed:
        await tracker.register_work("task-1", "existing-work", queue.name, **OWNER)
    loop = asyncio.get_running_loop()
    writing = asyncio.Event()
    release = threading.Event()
    messages = []
    enqueued_work: list[tuple[str, str]] = []

    def write(path, data, *, ctx):
        if committed:
            messages.append((path, data))
        loop.call_soon_threadsafe(writing.set)
        assert release.wait(timeout=5), "test did not release the writer"
        if not committed:
            raise OSError("write failed before persistence")
        return "message-1"

    client.write.side_effect = write
    with bind_task_context("task-1", "account", "user", enqueued_work=enqueued_work):
        enqueue = asyncio.create_task(
            queue.enqueue({"_task_work_id": work_id}, serial_key=serial_key)
        )
        try:
            await asyncio.wait_for(writing.wait(), timeout=5)
            assert await tracker.get_work_state("task-1", work_id, **OWNER) is WorkState.PENDING
            enqueue.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(enqueue, timeout=5)
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(enqueue, return_exceptions=True), timeout=5)

    assert client.write.call_count == 1
    if not committed:
        assert messages == []
        assert await tracker.get_work_state("task-1", work_id, **OWNER) is None
        assert await tracker.get_work_state("task-1", "existing-work", **OWNER) is WorkState.PENDING
        assert enqueued_work == []
        await tracker.mark_work_done("task-1", "existing-work", **OWNER)
        task = await tracker.get("task-1", **OWNER)
        assert task is not None and task.status is TaskStatus.FAILED
        assert task.error == "Task operation interrupted before its result was persisted"
        return

    [(path, data)] = messages
    message_id = "message-1"
    if serial_key is None:
        assert path == "/queue/Embedding/enqueue"
    else:
        assert path == "/queue/Embedding/enqueue_ext"
        _, _, _, payload_offset, message_id_bytes = struct.unpack_from("!4sHHI16s", data)
        message_id = str(UUID(bytes=message_id_bytes))
        data = data[payload_offset:]
    message = {"id": message_id, "data": data.decode()}
    metadata = extract_task_metadata(message)
    assert metadata is not None and metadata.work_id == work_id
    assert await tracker.get_work_state("task-1", work_id, **OWNER) is WorkState.PENDING
    assert enqueued_work == [("Embedding", work_id)]

    handler = AsyncMock(spec=DequeueHandlerBase)
    handler.on_dequeue.return_value = ProcessResult.success()
    queue.set_dequeue_handler(handler)
    assert (await queue.process_dequeued(message)).outcome is ProcessOutcome.SUCCESS
    assert await tracker.get_work_state("task-1", work_id, **OWNER) is WorkState.DONE


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


async def test_terminal_task_settles_redelivery_without_handler(tracked_queue):
    queue, tracker, transport = tracked_queue
    message = await enqueue_task(queue, tracker, transport)
    metadata = extract_task_metadata(message)
    await tracker.complete("task-1", {"done": True}, **OWNER)
    await tracker.mark_work_done("task-1", metadata.work_id, **OWNER)
    before = await tracker.get_with_events("task-1", **OWNER)
    handler = AsyncMock(spec=DequeueHandlerBase)
    queue.set_dequeue_handler(handler)
    transport.read.return_value = json.dumps(message).encode()
    transport.write.reset_mock()

    await queue.dequeue()

    handler.on_dequeue.assert_not_awaited()
    handler.on_cancelled.assert_not_awaited()
    transport.write.assert_awaited_once_with("/queue/Embedding/ack", b"message-1")
    after = await tracker.get_with_events("task-1", **OWNER)
    assert after.to_dict(include_events=True) == before.to_dict(include_events=True)
    assert await tracker.get_work_state("task-1", metadata.work_id, **OWNER) is WorkState.DONE


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


async def test_running_cancellation_waits_for_handler_cleanup(tracked_queue):
    queue, tracker, transport = tracked_queue
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


@pytest.mark.parametrize("worker", ["serial", "concurrent"])
@pytest.mark.parametrize("failure", ["business", "handoff", "replacement", "persist", "shutdown"])
async def test_worker_exception_ack_policy(tracked_queue, monkeypatch, worker, failure):
    queue, tracker, transport = tracked_queue
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
                await asyncio.wait_for(queue.dequeue(), timeout=2)
        else:
            await asyncio.wait_for(queue.dequeue(), timeout=2)
    else:
        manager = QueueManager(object())
        manager._poll_interval = 0.001
        manager._REQUEUE_POLL_INTERVAL = 0.001
        await asyncio.wait_for(manager._worker_async_concurrent(queue, stop, 2), 1)

    acks = [call for call in transport.write.await_args_list if call.args[0].endswith("/ack")]
    task = await tracker.get("task-1", **OWNER)
    assert stop.is_set()
    assert acks == []
    assert task.status is TaskStatus.RUNNING
    assert task.error is None
    if failure in ("business", "handoff", "replacement"):
        work = await tracker._store.get_work("task-1", metadata.work_id, **OWNER)
        assert work.state is WorkState.RUNNING
        assert (
            work.error
            == {
                "business": "invalid business input",
                "handoff": "coverage busy",
                "replacement": "replacement unavailable",
            }[failure]
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
