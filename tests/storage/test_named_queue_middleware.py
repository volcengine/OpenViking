# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue middleware ordering and transport compatibility."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.storage.queuefs.named_queue import (
    DequeueHandlerBase,
    NamedQueue,
)
from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult
from openviking.storage.queuefs.queue_middleware import QueueMiddleware


@pytest.fixture
def transport(monkeypatch):
    client = AsyncMock(spec=AsyncAGFSClient)
    client.write.return_value = "message-1"
    client.read.return_value = b"[]"
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda _client: client
    )
    return client


class Handler(DequeueHandlerBase):
    async def on_dequeue(self, data):
        return ProcessResult.success(data)


@pytest.mark.parametrize("operation", ["enqueue", "process", "ack", "clear"])
async def test_middleware_constructor_order_and_commit(transport, operation):
    events = []

    class Recorder(QueueMiddleware):
        def __init__(self, name):
            self.name = name

        async def invoke(self, ctx, call_next):
            events.append((self.name, "enter"))
            result = await call_next(ctx)
            if operation != "process":
                assert ctx.committed
            events.append((self.name, "exit"))
            return result

        enqueue = process = ack = clear = invoke

    middlewares = [Recorder("outer"), Recorder("inner")]
    queue = NamedQueue(
        object(), "/queue", "Test", dequeue_handler=Handler(), middlewares=middlewares
    )
    middlewares.clear()
    message = {"id": "message-1", "data": "{}"}
    if operation == "enqueue":
        assert await queue.enqueue({"value": 1}) == "message-1"
        transport.write.assert_awaited_once_with("/queue/Test/enqueue", b'{"value": 1}')
    elif operation == "process":
        assert (await queue.process_dequeued(message)).value == message
        transport.write.assert_not_awaited()
    elif operation == "ack":
        await queue.ack("message-1", message)
        transport.write.assert_awaited_once_with("/queue/Test/ack", b"message-1")
    else:
        assert await queue.clear()
        transport.write.assert_awaited_once_with("/queue/Test/clear", b"")
    assert events == [("outer", "enter"), ("inner", "enter"), ("inner", "exit"), ("outer", "exit")]


@pytest.mark.parametrize("failure", [RuntimeError("write failed"), asyncio.CancelledError()])
async def test_enqueue_failure_unwinds_middlewares(transport, failure):
    events = []

    class Recorder(QueueMiddleware):
        def __init__(self, name):
            self.name = name

        async def enqueue(self, ctx, call_next):
            events.append(self.name)
            try:
                return await call_next(ctx)
            finally:
                assert not ctx.committed
                events.append(f"{self.name}-cleanup")

    transport.write.side_effect = failure
    queue = NamedQueue(
        object(), "/queue", "Test", middlewares=[Recorder("outer"), Recorder("inner")]
    )
    with pytest.raises(type(failure)):
        await queue.enqueue("payload")
    assert events == ["outer", "inner", "inner-cleanup", "outer-cleanup"]


async def test_enqueue_middleware_rewrites_payload(transport):
    class Rewrite(QueueMiddleware):
        async def enqueue(self, ctx, call_next):
            assert ctx.payload == "input"
            ctx.payload = {"input": ctx.payload, "middleware": True}
            return await call_next(ctx)

    queue = NamedQueue(object(), "/queue", "Test", middlewares=[Rewrite()])
    await queue.enqueue("input")
    assert json.loads(transport.write.await_args.args[1]) == {"input": "input", "middleware": True}


@pytest.mark.parametrize("failure", [RuntimeError("handler failed"), asyncio.CancelledError()])
async def test_handler_exception_still_skips_ack(transport, failure):
    class FailingHandler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            raise failure

    transport.read.return_value = b'{"id":"message-1","data":"{}"}'
    queue = NamedQueue(
        object(),
        "/queue",
        "Test",
        dequeue_handler=FailingHandler(),
        middlewares=[QueueMiddleware()],
    )
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await queue.dequeue()
    else:
        assert await queue.dequeue() is None
    transport.write.assert_not_awaited()


async def test_empty_success_result_still_acks(transport):
    class DiscardHandler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            return ProcessResult.success()

    transport.read.return_value = b'{"id":"message-1","data":"{}"}'
    queue = NamedQueue(
        object(),
        "/queue",
        "Test",
        dequeue_handler=DiscardHandler(),
        middlewares=[QueueMiddleware()],
    )
    assert await queue.dequeue() is None
    transport.write.assert_awaited_once_with("/queue/Test/ack", b"message-1")


async def test_clear_snapshot_error_is_not_converted_to_success(transport):
    transport.read.side_effect = OSError("snapshot unavailable")
    queue = NamedQueue(object(), "/queue", "Test", middlewares=[QueueMiddleware()])
    with pytest.raises(OSError, match="snapshot unavailable"):
        await queue.clear()
    transport.write.assert_not_awaited()


async def test_queue_without_middleware_remains_generic(transport):
    queue = NamedQueue(object(), "/queue", "Test")
    payload = {"task_id": "opaque-business-field"}
    await queue.enqueue(payload)
    assert json.loads(transport.write.await_args.args[1]) == payload
    assert (await queue.process_dequeued(payload)).value == payload
    transport.write.reset_mock()
    await queue.ack("")
    transport.write.assert_not_awaited()
    assert await queue.clear()
    transport.read.assert_not_awaited()


@pytest.mark.parametrize(
    "result",
    [
        ProcessResult.success(),
        ProcessResult.failed("bad input"),
        ProcessResult.requeued(),
        ProcessResult.cancelled(),
    ],
)
async def test_process_short_circuit_settles_once_without_acking(transport, result):
    class ShortCircuit(QueueMiddleware):
        async def process(self, ctx, call_next):
            return result

    queue = NamedQueue(
        object(),
        "/queue",
        "Test",
        dequeue_handler=Handler(),
        middlewares=[ShortCircuit()],
    )
    message = {"id": "m", "data": "{}"}
    assert await queue.process_dequeued(message) == result
    assert queue._error_count == (result.outcome is ProcessOutcome.FAILED)
    assert queue._processed == (result.outcome is not ProcessOutcome.FAILED)
    assert queue._requeue_count == (result.outcome is ProcessOutcome.REQUEUED)
    transport.write.assert_not_awaited()


async def test_cancel_before_process_starts_leaves_no_local_inflight_state(transport):
    queue = NamedQueue(object(), "/queue", "Test", dequeue_handler=Handler())
    task = asyncio.create_task(queue.process_dequeued({"id": "m"}))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    transport.write.assert_not_awaited()


async def test_invalid_process_result_does_not_ack(transport):
    class Invalid(QueueMiddleware):
        async def process(self, ctx, call_next):
            return None

    queue = NamedQueue(
        object(),
        "/queue",
        "Test",
        dequeue_handler=Handler(),
        middlewares=[Invalid()],
    )
    with pytest.raises(TypeError, match="ProcessResult"):
        await queue.process_dequeued({"id": "m"})
    assert queue._error_count == 1
    transport.write.assert_not_awaited()


async def test_ack_error_does_not_double_count_processing(transport):
    transport.read.return_value = b'{"id":"m","data":"{}"}'
    transport.write.side_effect = OSError("ack failed")
    queue = NamedQueue(object(), "/queue", "Test", dequeue_handler=Handler())
    await queue.dequeue()
    assert queue._processed == 1
    assert queue._error_count == 0


async def test_clear_short_circuit_reports_no_commit(transport):
    class SkipClear(QueueMiddleware):
        async def clear(self, ctx, call_next):
            return None

    queue = NamedQueue(object(), "/queue", "Test", middlewares=[SkipClear()])
    assert await queue.clear() is False
    transport.write.assert_not_awaited()


async def test_dequeue_without_handler_bypasses_process_middleware_and_acks(transport):
    calls = []

    class Recorder(QueueMiddleware):
        async def process(self, ctx, call_next):
            calls.append(ctx.message)
            return await call_next(ctx)

    message = {"id": "m", "data": "{}"}
    transport.read.return_value = json.dumps(message).encode()
    queue = NamedQueue(object(), "/queue", "Test", middlewares=[Recorder()])

    assert await queue.dequeue() == message
    assert calls == []
    assert queue._processed == 0
    transport.write.assert_awaited_once_with("/queue/Test/ack", b"m")
