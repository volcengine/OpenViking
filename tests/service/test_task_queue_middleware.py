# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Legacy task-index behavior through the middleware boundary."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_work_index import (
    TaskWorkIndex,
    TaskWorkRejected,
    bind_task_context,
    extract_task_metadata,
    get_task_context,
)
from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult
from openviking.storage.queuefs.queue_middleware import QueueMiddleware
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor


@pytest.fixture
def tracked_queue(monkeypatch):
    transport = AsyncMock(spec=AsyncAGFSClient)
    transport.write.return_value = "message-1"
    # Tests override transport.read.return_value to feed /messages snapshots;
    # status reads must still return the backend JSON shape.
    transport.read.return_value = b"0"

    async def read(path):
        if path.endswith("/status"):
            return b'{"pending":0,"processing":0}'
        value = transport.read.return_value
        return value

    transport.read.side_effect = read
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda _client: transport
    )
    index = TaskWorkIndex()
    finalize = AsyncMock()
    cancelled = set()
    index.set_callbacks(
        finalize_before_ack=finalize,
        is_cancellation_requested=lambda task_id: task_id in cancelled,
    )
    queue = NamedQueue(object(), "/queue", "Test", middlewares=[TaskWorkQueueMiddleware(index)])
    return queue, index, transport, finalize, cancelled


async def enqueue_task(queue, transport, task_id="task-1"):
    with bind_task_context(task_id, "account", "user"):
        await queue.enqueue({"value": 1})
    return {"id": "message-1", "data": transport.write.await_args.args[1].decode()}


async def test_enqueue_registers_before_write_and_ack_finalizes_before_delete(tracked_queue):
    queue, index, transport, finalize, _ = tracked_queue

    async def write(path, data):
        assert path.endswith("/enqueue")
        assert index.has_work("task-1")
        return "message-1"

    transport.write.side_effect = write
    message = await enqueue_task(queue, transport)
    metadata = extract_task_metadata(message)
    assert metadata.task_id == "task-1"
    assert metadata.account_id == "account"
    assert metadata.user_id == "user"
    assert get_task_context() is None

    async def ack_write(path, data):
        assert path.endswith("/ack")
        assert not index.has_work("task-1")
        finalize.assert_awaited_once_with(metadata)
        return ""

    transport.write.side_effect = ack_write
    await queue.ack("message-1", message)
    assert not index.has_work("task-1")


@pytest.mark.parametrize("failure", [OSError("write failed"), asyncio.CancelledError()])
async def test_enqueue_failure_discards_registered_work(tracked_queue, failure):
    queue, index, transport, finalize, _ = tracked_queue
    transport.write.side_effect = failure
    with bind_task_context("task-1", "account", "user"):
        with pytest.raises(type(failure)):
            await queue.enqueue({})
    assert not index.has_work("task-1")
    finalize.assert_awaited_once()


async def test_cancelled_task_rejects_enqueue_without_write(tracked_queue):
    queue, index, transport, _, cancelled = tracked_queue
    cancelled.add("task-1")
    with bind_task_context("task-1", "account", "user"):
        with pytest.raises(TaskWorkRejected, match="cancelling"):
            await queue.enqueue({})
    transport.write.assert_not_awaited()
    assert not index.has_work("task-1")


@pytest.mark.parametrize("operation", ["ack", "clear"])
@pytest.mark.parametrize("failure", [OSError("write failed"), asyncio.CancelledError()])
async def test_failed_ack_or_clear_restores_work(tracked_queue, operation, failure):
    queue, index, transport, finalize, _ = tracked_queue
    message = await enqueue_task(queue, transport)
    transport.read.return_value = json.dumps([message]).encode()
    transport.write.reset_mock()
    transport.write.side_effect = failure

    async def invoke():
        if operation == "ack":
            return await queue.ack("message-1", message)
        return await queue.clear()

    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await invoke()
    elif operation == "clear":
        assert await invoke() is False
    else:
        assert await invoke() is None
    finalize.assert_awaited_once()
    assert index.has_work("task-1")


@pytest.mark.parametrize("operation", ["ack", "clear"])
async def test_finalization_failure_prevents_transport_delete(tracked_queue, operation):
    queue, index, transport, finalize, _ = tracked_queue
    message = await enqueue_task(queue, transport)
    transport.read.return_value = json.dumps([message]).encode()
    transport.write.reset_mock()
    finalize.side_effect = OSError("task store unavailable")
    if operation == "ack":
        await queue.ack("message-1", message)
    else:
        assert await queue.clear() is False
    transport.write.assert_not_awaited()
    assert index.has_work("task-1")


@pytest.mark.parametrize("operation", ["enqueue", "ack", "clear"])
async def test_inner_short_circuit_compensates_uncommitted_index_change(tracked_queue, operation):
    queue, index, transport, _, _ = tracked_queue

    class ShortCircuit(QueueMiddleware):
        async def invoke(self, ctx, call_next):
            return "deduplicated"

    setattr(ShortCircuit, operation, ShortCircuit.invoke)
    queue = NamedQueue(
        object(), "/queue", "Test", middlewares=[TaskWorkQueueMiddleware(index), ShortCircuit()]
    )
    message = await enqueue_task(queue, transport) if operation != "enqueue" else None
    transport.write.reset_mock()
    if operation == "enqueue":
        with bind_task_context("task-1", "account", "user"):
            assert await queue.enqueue({}) == "deduplicated"
        assert not index.has_work("task-1")
    elif operation == "ack":
        await queue.ack("message-1", message)
        assert index.has_work("task-1")
    else:
        transport.read.return_value = json.dumps([message]).encode()
        await queue.clear()
        assert index.has_work("task-1")
    transport.write.assert_not_awaited()


@pytest.mark.parametrize("operation", ["enqueue", "ack", "clear"])
async def test_post_commit_error_does_not_undo_index_change(tracked_queue, operation):
    queue, index, transport, _, _ = tracked_queue

    class FailAfter(QueueMiddleware):
        async def invoke(self, ctx, call_next):
            await call_next(ctx)
            raise RuntimeError("after commit")

    setattr(FailAfter, operation, FailAfter.invoke)
    queue = NamedQueue(
        object(), "/queue", "Test", middlewares=[TaskWorkQueueMiddleware(index), FailAfter()]
    )
    if operation == "enqueue":
        with bind_task_context("task-1", "account", "user"):
            with pytest.raises(RuntimeError, match="after commit"):
                await queue.enqueue({})
        assert index.has_work("task-1")
    else:
        message = await enqueue_task(queue, transport)
        transport.read.return_value = json.dumps([message]).encode()
        if operation == "ack":
            await queue.ack("message-1", message)
        else:
            assert await queue.clear() is False
        assert not index.has_work("task-1")


async def test_clear_rolls_back_all_prepared_messages(tracked_queue):
    queue, index, transport, finalize, _ = tracked_queue
    first = await enqueue_task(queue, transport, "first")
    second = await enqueue_task(queue, transport, "second")
    transport.read.return_value = json.dumps([first, second]).encode()

    async def finish(metadata):
        if metadata.task_id == "second":
            raise OSError("second task failed")

    finalize.side_effect = finish
    transport.write.reset_mock()
    assert await queue.clear() is False
    transport.write.assert_not_awaited()
    assert index.has_work("first") and index.has_work("second")
    finalize.side_effect = None
    assert await queue.clear()
    assert not index.has_work("first") and not index.has_work("second")


async def test_process_result_tracks_children_and_errors(tracked_queue):
    queue, index, transport, _, _ = tracked_queue
    message = await enqueue_task(queue, transport)
    contexts = []

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            contexts.append(get_task_context())
            await queue.enqueue({"child": True})
            return ProcessResult.failed("failed work")

    queue.set_dequeue_handler(Handler())
    assert (await queue.process_dequeued(message)).outcome is ProcessOutcome.FAILED
    assert contexts[0].task_id == "task-1"
    assert get_task_context() is None
    assert index.failure("task-1") == "failed work"
    child_payload = next(
        call.args[1]
        for call in reversed(transport.write.await_args_list)
        if call.args[0].endswith("/enqueue")
    )
    child = {"id": "child", "data": child_payload.decode()}
    assert index.has_work("task-1")
    await queue.ack("message-1", message)
    assert index.has_work("task-1")
    await queue.ack("child", child)
    assert not index.has_work("task-1")
    status = await queue.get_status()
    assert status.error_count == 1
    assert status.in_progress == 0


async def test_handler_exception_records_task_failure_and_propagates(tracked_queue):
    queue, index, transport, _, _ = tracked_queue
    message = await enqueue_task(queue, transport)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            raise RuntimeError("handler failed")

    queue.set_dequeue_handler(Handler())
    with pytest.raises(RuntimeError, match="handler failed"):
        await queue.process_dequeued(message)
    assert index.failure("task-1") == "handler failed"
    assert index.has_work("task-1")


async def test_concurrent_results_update_each_task(tracked_queue):
    queue, index, transport, _, _ = tracked_queue
    first = await enqueue_task(queue, transport, "first")
    second = await enqueue_task(queue, transport, "second")
    both_started = asyncio.Event()
    started = 0

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            task_id = get_task_context().task_id
            return ProcessResult.failed(f"{task_id} failed")

    queue.set_dequeue_handler(Handler())
    await asyncio.wait_for(
        asyncio.gather(queue.process_dequeued(first), queue.process_dequeued(second)),
        timeout=1,
    )
    assert index.failure("first") == "first failed"
    assert index.failure("second") == "second failed"


async def test_cancel_before_handler_uses_legacy_discard_callback(tracked_queue):
    queue, index, transport, _, cancelled = tracked_queue
    message = await enqueue_task(queue, transport)
    cancelled.add("task-1")
    calls = []

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            pytest.fail("cancelled task reached handler")

        async def on_cancelled(self, data):
            calls.append(data)
            return await super().on_cancelled(data)

    queue.set_dequeue_handler(Handler())
    assert (await queue.process_dequeued(message)).outcome is ProcessOutcome.CANCELLED
    assert calls == [message]
    await queue.ack("message-1", message)
    assert not index.has_work("task-1")
    status = await queue.get_status()
    assert status.processed == 1 and status.in_progress == 0


@pytest.mark.parametrize("user_cancel", [True, False])
async def test_active_cancellation_distinguishes_user_from_shutdown(tracked_queue, user_cancel):
    queue, index, transport, _, cancelled = tracked_queue
    message = await enqueue_task(queue, transport)
    started = asyncio.Event()

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            started.set()
            await asyncio.Event().wait()

    queue.set_dequeue_handler(Handler())
    running = asyncio.create_task(queue.process_dequeued(message))
    await asyncio.wait_for(started.wait(), timeout=1)
    try:
        if user_cancel:
            cancelled.add("task-1")
            index.cancel_active("task-1")
            result = await asyncio.wait_for(running, timeout=1)
            assert result.outcome is ProcessOutcome.CANCELLED
            await queue.ack("message-1", message)
            assert not index.has_work("task-1")
            assert (await queue.get_status()).processed == 1
        else:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            assert index.has_work("task-1")
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


async def test_shared_middleware_keeps_concurrent_contexts_isolated(tracked_queue):
    queue, index, transport, _, _ = tracked_queue
    first = await enqueue_task(queue, transport, "first")
    second = await enqueue_task(queue, transport, "second")
    both_started = asyncio.Event()
    seen = []

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            before = get_task_context().task_id
            seen.append(before)
            if len(seen) == 2:
                both_started.set()
            await both_started.wait()
            assert get_task_context().task_id == before
            return ProcessResult.success(data)

    queue.set_dequeue_handler(Handler())
    await asyncio.wait_for(
        asyncio.gather(queue.process_dequeued(first), queue.process_dequeued(second)), timeout=1
    )
    assert set(seen) == {"first", "second"}
    assert get_task_context() is None
    await queue.ack("first", first)
    await queue.ack("second", second)
    assert not index.has_work("first") and not index.has_work("second")


async def test_awaited_child_result_is_recorded_before_ack(tracked_queue):
    queue, index, transport, finalize, _ = tracked_queue
    message = await enqueue_task(queue, transport)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            async def child():
                await asyncio.sleep(0)
                return ProcessResult.failed("child failure")

            return await asyncio.create_task(child())

    async def finish(metadata):
        assert index.failure(metadata.task_id) == "child failure"

    finalize.side_effect = finish
    queue.set_dequeue_handler(Handler())
    await queue.process_dequeued(message)
    finalize.assert_not_awaited()
    await queue.ack("message-1", message)
    finalize.assert_awaited_once()


async def test_cancel_cleanup_interruption_does_not_ack(tracked_queue):
    queue, index, transport, _, cancelled = tracked_queue
    message = await enqueue_task(queue, transport)
    cancelled.add("task-1")
    transport.write.reset_mock()

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            pytest.fail("cancelled work must not execute")

        async def on_cancelled(self, data):
            raise asyncio.CancelledError

    queue.set_dequeue_handler(Handler())
    with pytest.raises(asyncio.CancelledError):
        await queue.process_dequeued(message)
    assert index.has_work("task-1")
    transport.write.assert_not_awaited()


async def test_cross_loop_cancellation_waits_for_handler_cleanup(tracked_queue):
    queue, index, transport, _, cancelled = tracked_queue
    started = threading.Event()
    cleanup_started = threading.Event()
    cleanup_release = threading.Event()
    request_loop = asyncio.get_running_loop()

    class Session:
        async def exists(self):
            return True

        async def load(self):
            pass

        async def resume_queued_commit(self, msg):
            assert asyncio.get_running_loop() is not request_loop
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await asyncio.to_thread(cleanup_release.wait)

    class Service:
        def session(self, ctx, session_id, session_uri=None):
            return Session()

    from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
    from openviking_cli.session.user_id import UserIdentifier

    msg = SessionCommitMsg(
        task_id="task-1",
        session_id="session-1",
        session_uri="viking://session/session-1",
        archive_uri="viking://session/session-1/history/archive-1",
        user=UserIdentifier("account", "user").to_dict(),
    )
    with bind_task_context("task-1", "account", "user"):
        await queue.enqueue(msg.to_dict())
    message = {"id": "m", "data": transport.write.await_args.args[1].decode()}
    transport.write.reset_mock()
    queue.set_dequeue_handler(SessionCommitProcessor(Service()))

    def consume():
        return asyncio.run(queue.process_dequeued(message))

    worker = asyncio.create_task(asyncio.to_thread(consume))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        cancelled.add("task-1")
        index.cancel_active("task-1")
        assert await asyncio.to_thread(cleanup_started.wait, 3)
        assert not worker.done()
        transport.write.assert_not_awaited()
    finally:
        cleanup_release.set()
        if not cleanup_started.is_set():
            cancelled.add("task-1")
            index.cancel_active("task-1")
        result = await asyncio.wait_for(worker, timeout=3)
    assert result.outcome is ProcessOutcome.CANCELLED
    transport.write.assert_not_awaited()
    await queue.ack("m", message)
    assert not index.has_work("task-1")
    transport.write.assert_awaited_once_with("/queue/Test/ack", b"m")
