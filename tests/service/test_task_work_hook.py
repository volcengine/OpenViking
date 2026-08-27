# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the TaskTracker adapter over generic queue hooks."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.service.task_context import bind_task_context
from openviking.service.task_domain import TaskStatus, WorkState
from openviking.service.task_store import CachingTaskWorkStore, PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.service.task_work_hook import (
    TASK_ACCOUNT_ID_FIELD,
    TASK_USER_ID_FIELD,
    TASK_WORK_ID_FIELD,
    TaskWorkQueueMiddleware,
    enqueue_with_task_work,
    extract_task_metadata,
    install_task_work_tracking,
)
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.queue_hook import (
    AckContext,
    DiscardReason,
    EnqueueContext,
    EnqueueKind,
    ProcessContext,
    ProcessOutcome,
    ProcessResult,
    QueueEnqueueRejected,
    QueueMiddleware,
)


class _Agfs:
    def __init__(self):
        self.files = {}

    def mkdir(self, path, mode="755"):
        return None

    def write(self, path, data):
        self.files[path] = data
        return "OK"

    def read(self, path, offset=0, size=-1, stream=False):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def ls(self, path="/"):
        return [{"path": key} for key in self.files if key.startswith(path + "/")]

    def rm(self, path, recursive=False, force=True):
        self.files.pop(path, None)


async def _middleware_enqueue(
    middleware,
    queue,
    payload,
    *,
    kind=EnqueueKind.NEW,
):
    ctx = EnqueueContext(queue=queue, payload=payload, kind=kind)

    async def commit(current):
        current.committed_msg_id = "message-1"
        return current.committed_msg_id

    await middleware.enqueue(ctx, commit)
    return ctx.payload


def _process_context(queue, message, discard=None):
    async def default_discard(_reason, _handler_started):
        return ProcessResult.cancelled()

    return ProcessContext(
        queue=queue,
        message=message,
        _discard=discard or default_discard,
    )


@pytest.mark.asyncio
async def test_process_finalizes_work_and_ack_does_not_reopen_it():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("add_resource", account_id="a", user_id="u")
    await tracker.start(task.task_id, account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)

    payload = await _middleware_enqueue(
        middleware,
        "Semantic",
        {"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )
    await tracker.complete(task.task_id, {"ok": True}, account_id="a", user_id="u")
    result = await middleware.process(
        _process_context("Semantic", payload),
        AsyncMock(return_value=ProcessResult.success()),
    )

    completed = await tracker.get(task.task_id, account_id="a", user_id="u")
    assert result.outcome is ProcessOutcome.SUCCESS
    assert completed.status == TaskStatus.COMPLETED
    assert (await tracker.queue_status(task.task_id, account_id="a", user_id="u"))["Semantic"][
        "processed"
    ] == 1

    async def failed_ack(_ctx):
        raise OSError("ack failed")

    with pytest.raises(OSError, match="ack failed"):
        await middleware.ack(
            AckContext("Semantic", payload, "message-1"),
            failed_ack,
        )
    still_completed = await tracker.get(task.task_id, account_id="a", user_id="u")
    assert still_completed.status == TaskStatus.COMPLETED
    assert not await tracker.has_work(task.task_id, account_id="a", user_id="u")


@pytest.mark.asyncio
async def test_task_response_does_not_expose_internal_fields():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("add_resource", account_id="a", user_id="u")
    payload = task.to_dict()
    assert set(payload) == {
        "task_id",
        "task_type",
        "status",
        "created_at",
        "updated_at",
        "created_at_iso",
        "updated_at_iso",
        "resource_id",
        "meta",
        "stage",
        "result",
        "error",
    }


@pytest.mark.asyncio
async def test_public_queue_status_keeps_legacy_shape_and_hides_internal_queues():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("add_resource", account_id="a", user_id="u")
    await tracker.register_work(
        task.task_id, "internal", "AddResource", account_id="a", user_id="u"
    )
    status = await tracker.queue_status(task.task_id, account_id="a", user_id="u")
    assert status == {
        "Semantic": {
            "processed": 0,
            "requeue_count": 0,
            "error_count": 0,
            "errors": [],
        },
        "Embedding": {
            "processed": 0,
            "requeue_count": 0,
            "error_count": 0,
            "errors": [],
        },
    }


@pytest.mark.asyncio
async def test_wait_for_descendants_excludes_the_current_queue_work():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("session_commit", account_id="a", user_id="u")
    await tracker.register_work(
        task.task_id, "session-work", "SessionCommit", account_id="a", user_id="u"
    )
    await tracker.register_work(
        task.task_id, "embedding-work", "Embedding", account_id="a", user_id="u"
    )

    waiting = asyncio.create_task(
        tracker.wait_for_descendants(task.task_id, "session-work", account_id="a", user_id="u")
    )
    await asyncio.sleep(0)
    assert not waiting.done()

    await tracker.settle_work(task.task_id, "embedding-work", account_id="a", user_id="u")
    await asyncio.wait_for(waiting, timeout=1)

    assert await tracker.has_work(task.task_id, account_id="a", user_id="u")


@pytest.mark.asyncio
async def test_startup_work_restore_creates_and_then_upgrades_placeholder_task():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    await tracker.restore_work(
        "legacy-task",
        "queuefs:message-1",
        "AddResource",
        account_id="a",
        user_id="u",
    )

    placeholder = await tracker.get("legacy-task", account_id="a", user_id="u")
    assert placeholder is not None
    assert await tracker.has_work("legacy-task", account_id="a", user_id="u")

    upgraded = await tracker.create(
        "add_resource",
        task_id="legacy-task",
        account_id="a",
        user_id="u",
        resource_id="viking://resources/restored",
    )
    assert upgraded.task_type == "add_resource"
    assert upgraded.resource_id == "viking://resources/restored"
    assert await tracker.has_work("legacy-task", account_id="a", user_id="u")


@pytest.mark.asyncio
async def test_rejected_domain_enqueue_does_not_leave_registered_work():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("add_resource", account_id="a", user_id="u")

    class DedupeMiddleware(QueueMiddleware):
        async def enqueue(self, ctx, call_next):
            del ctx, call_next
            raise QueueEnqueueRejected("deduplicated")

    # Domain hook is first in production SemanticQueue, so task registration is
    # never attempted for a deduplicated payload.
    queue = NamedQueue(
        None,
        "/queue",
        "Semantic",
        middlewares=[DedupeMiddleware(), TaskWorkQueueMiddleware(tracker)],
    )
    queue._initialized = True
    result = await queue.enqueue({"task_id": task.task_id, "account_id": "a", "user_id": "u"})
    assert result == "deduplicated"
    assert not await tracker.has_work(task.task_id, account_id="a", user_id="u")


@pytest.mark.asyncio
async def test_task_context_stamps_task_owner_without_overwriting_business_owner():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create(
        "add_resource", task_id="scoped", account_id="task-account", user_id="actor"
    )
    middleware = TaskWorkQueueMiddleware(tracker)

    with bind_task_context(task.task_id, "task-account", "actor"):
        payload = await _middleware_enqueue(
            middleware,
            "Semantic",
            {
                "task_id": task.task_id,
                "account_id": "content-account",
                "user_id": "content-owner",
            },
        )

    assert payload["account_id"] == "content-account"
    assert payload["user_id"] == "content-owner"
    assert payload[TASK_ACCOUNT_ID_FIELD] == "task-account"
    assert payload[TASK_USER_ID_FIELD] == "actor"
    metadata = extract_task_metadata(payload)
    assert metadata is not None
    assert (metadata.account_id, metadata.user_id) == ("task-account", "actor")


@pytest.mark.asyncio
async def test_duplicate_register_work_does_not_reset_terminal_work():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("content_write", account_id="a", user_id="u")
    await tracker.register_work(
        task.task_id,
        "finished-work",
        "Embedding",
        account_id="a",
        user_id="u",
    )
    await tracker.register_work(
        task.task_id,
        "open-work",
        "Embedding",
        account_id="a",
        user_id="u",
    )
    await tracker.mark_work_done(
        task.task_id,
        "finished-work",
        account_id="a",
        user_id="u",
    )

    assert await tracker.register_work(
        task.task_id,
        "finished-work",
        "Embedding",
        account_id="a",
        user_id="u",
    )

    assert (
        await tracker.get_work_state(
            task.task_id,
            "finished-work",
            account_id="a",
            user_id="u",
        )
        is WorkState.DONE
    )
    status = await tracker.queue_status(task.task_id, account_id="a", user_id="u")
    assert status["Embedding"]["processed"] == 1
    assert status["Embedding"]["error_count"] == 0


@pytest.mark.asyncio
async def test_enqueue_with_task_work_marks_false_result_failed_without_bound_context():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    set_task_tracker(tracker)
    try:
        task = await tracker.create("content_write", account_id="a", user_id="u")
        payload = {"task_id": task.task_id, "account_id": "a", "user_id": "u"}

        async def reject_enqueue(_payload):
            return False

        result = await enqueue_with_task_work(
            payload,
            "Embedding",
            reject_enqueue,
            false_failure_message="embedding enqueue returned false",
        )

        assert result is False
        metadata = extract_task_metadata(payload)
        assert metadata is not None
        assert (
            await tracker.get_work_state(
                task.task_id,
                metadata.work_id,
                account_id="a",
                user_id="u",
            )
            is WorkState.FAILED
        )
        status = await tracker.queue_status(task.task_id, account_id="a", user_id="u")
        assert status["Embedding"]["errors"] == [{"message": "embedding enqueue returned false"}]
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_enqueue_with_task_work_ignores_falsy_result_without_false_message():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    set_task_tracker(tracker)
    try:
        task = await tracker.create("content_write", account_id="a", user_id="u")
        payload = {"task_id": task.task_id, "account_id": "a", "user_id": "u"}

        async def enqueue_empty_id(_payload):
            return ""

        result = await enqueue_with_task_work(payload, "Semantic", enqueue_empty_id)

        assert result == ""
        metadata = extract_task_metadata(payload)
        assert metadata is not None
        assert (
            await tracker.get_work_state(
                task.task_id,
                metadata.work_id,
                account_id="a",
                user_id="u",
            )
            is WorkState.PENDING
        )
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_enqueue_with_task_work_marks_exception_message_failed():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    set_task_tracker(tracker)
    try:
        task = await tracker.create("content_write", account_id="a", user_id="u")
        payload = {"task_id": task.task_id, "account_id": "a", "user_id": "u"}

        async def fail_enqueue(_payload):
            raise RuntimeError("queue unavailable")

        with pytest.raises(RuntimeError, match="queue unavailable"):
            await enqueue_with_task_work(
                payload,
                "Embedding",
                fail_enqueue,
                false_failure_message="embedding enqueue returned false",
            )

        status = await tracker.queue_status(task.task_id, account_id="a", user_id="u")
        assert status["Embedding"]["errors"] == [{"message": "queue unavailable"}]
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_enqueue_with_task_work_does_not_fail_done_duplicate():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    set_task_tracker(tracker)
    try:
        task = await tracker.create("content_write", account_id="a", user_id="u")
        await tracker.register_work(
            task.task_id,
            "finished-work",
            "Semantic",
            account_id="a",
            user_id="u",
        )
        await tracker.register_work(
            task.task_id,
            "open-work",
            "Semantic",
            account_id="a",
            user_id="u",
        )
        await tracker.mark_work_done(
            task.task_id,
            "finished-work",
            account_id="a",
            user_id="u",
        )
        payload = {
            "task_id": task.task_id,
            "account_id": "a",
            "user_id": "u",
            TASK_WORK_ID_FIELD: "finished-work",
        }

        async def reject_enqueue(_payload):
            return False

        await enqueue_with_task_work(
            payload,
            "Semantic",
            reject_enqueue,
        )

        assert (
            await tracker.get_work_state(
                task.task_id,
                "finished-work",
                account_id="a",
                user_id="u",
            )
            is WorkState.DONE
        )
        status = await tracker.queue_status(task.task_id, account_id="a", user_id="u")
        assert status["Semantic"]["processed"] == 1
        assert status["Semantic"]["error_count"] == 0
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_physical_enqueue_failure_is_visible_in_task_queue_status():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("content_write", account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)
    ctx = EnqueueContext(
        queue="Embedding",
        payload={"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )

    async def fail_write(_current):
        raise OSError("queuefs unavailable")

    with pytest.raises(OSError, match="queuefs unavailable"):
        await middleware.enqueue(ctx, fail_write)

    status = await tracker.queue_status(task.task_id, account_id="a", user_id="u")
    embedding_status = status["Embedding"]
    assert embedding_status["processed"] == 0
    assert embedding_status["error_count"] == 1
    assert embedding_status["errors"][0]["message"] == "queuefs unavailable"
    metadata = extract_task_metadata(ctx.payload)
    assert metadata is not None
    assert (
        await tracker.get_work_state(
            task.task_id,
            metadata.work_id,
            account_id="a",
            user_id="u",
        )
        is WorkState.FAILED
    )


def test_legacy_message_owner_fields_remain_readable():
    metadata = extract_task_metadata(
        {
            "id": "message-1",
            "data": {
                "task_id": "legacy-task",
                "account_id": "legacy-account",
                "user_id": "legacy-user",
            },
        }
    )
    assert metadata is not None
    assert metadata.work_id == "queuefs:message-1"
    assert (metadata.account_id, metadata.user_id) == ("legacy-account", "legacy-user")


@pytest.mark.asyncio
async def test_install_adapter_restores_snapshot_before_registering_hook():
    events = []

    class QueueManagerStub:
        async def snapshot_all(self):
            events.append("snapshot")
            return {
                "Semantic": [
                    {
                        "id": "message-1",
                        "data": {
                            "task_id": "task-1",
                            "account_id": "a",
                            "user_id": "u",
                        },
                    }
                ]
            }

        def register_middleware(self, middleware):
            events.append(("middleware", type(middleware).__name__))

    class TrackerStub:
        async def restore_work(self, task_id, work_id, queue_name, *, account_id, user_id):
            events.append(("restore", task_id, work_id, queue_name, account_id, user_id))

    await install_task_work_tracking(QueueManagerStub(), TrackerStub())

    assert events == [
        "snapshot",
        ("restore", "task-1", "queuefs:message-1", "Semantic", "a", "u"),
        ("middleware", "TaskWorkQueueMiddleware"),
    ]


@pytest.mark.asyncio
async def test_process_middlewares_unwind_in_onion_order():
    events = []

    class FirstMiddleware(QueueMiddleware):
        async def process(self, ctx, call_next):
            events.append("first-before")
            try:
                return await call_next(ctx)
            finally:
                events.append("first-after")

    class FailingMiddleware(QueueMiddleware):
        async def process(self, ctx, call_next):
            del ctx, call_next
            events.append("second-before")
            raise RuntimeError("before process failed")

    queue = NamedQueue(
        None,
        "/queue",
        "Semantic",
        middlewares=[FirstMiddleware(), FailingMiddleware()],
    )
    queue._dequeue_handler = AsyncMock()
    with pytest.raises(RuntimeError, match="before process failed"):
        await queue._invoke_process({"id": "message-1"})

    assert events == ["first-before", "second-before", "first-after"]


@pytest.mark.asyncio
async def test_dequeue_releases_local_counter_when_before_process_fails(monkeypatch):
    class FailingMiddleware(QueueMiddleware):
        async def process(self, ctx, call_next):
            del ctx, call_next
            raise RuntimeError("before process failed")

    queue = NamedQueue(
        None,
        "/queue",
        "UserDeletion",
        middlewares=[FailingMiddleware()],
    )
    queue._initialized = True
    queue._dequeue_handler = AsyncMock()

    async def read_message():
        return {"id": "message-1", "data": {}}

    monkeypatch.setattr(queue, "_read_queue_message", read_message)

    assert await queue.dequeue() is None
    assert queue._in_progress == 0
    assert queue._error_count == 1


@pytest.mark.asyncio
async def test_requeued_work_is_not_reclassified_as_failed_on_ack():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("add_resource", account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)
    payload = await _middleware_enqueue(
        middleware,
        "Semantic",
        {"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )

    await middleware.process(
        _process_context("Semantic", payload),
        AsyncMock(
            return_value=ProcessResult.requeue(
                payload,
                error="transient attempt failed",
            )
        ),
    )

    status = await tracker.queue_status(task.task_id, account_id="a", user_id="u")
    assert status["Semantic"] == {
        "processed": 0,
        "requeue_count": 1,
        "error_count": 0,
        "errors": [],
    }


@pytest.mark.asyncio
async def test_failed_result_is_persisted_before_ack_and_fails_task():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("content_write", account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)
    payload = await _middleware_enqueue(
        middleware,
        "Semantic",
        {"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )

    await middleware.process(
        _process_context("Semantic", payload),
        AsyncMock(return_value=ProcessResult.failed("handler failed")),
    )

    failed = await tracker.get(task.task_id, account_id="a", user_id="u")
    assert failed.status == TaskStatus.FAILED
    assert failed.error == "handler failed"


@pytest.mark.asyncio
async def test_handler_exception_is_failed_and_acknowledged(monkeypatch):
    class FailingHandler:
        async def on_dequeue(self, message):
            raise RuntimeError("handler failed")

    queue = NamedQueue(None, "/queue", "Semantic", dequeue_handler=FailingHandler())
    queue._initialized = True
    ack = AsyncMock()
    monkeypatch.setattr(queue, "ack", ack)
    monkeypatch.setattr(
        queue,
        "_read_queue_message",
        AsyncMock(return_value={"id": "message-1", "data": {}}),
    )

    assert await queue.dequeue() is None
    ack.assert_awaited_once()
    assert queue._in_progress == 0
    assert queue._error_count == 1


@pytest.mark.asyncio
async def test_shutdown_cancellation_is_not_acknowledged(monkeypatch):
    class CancelledHandler:
        async def on_dequeue(self, message):
            raise asyncio.CancelledError

    queue = NamedQueue(None, "/queue", "Semantic", dequeue_handler=CancelledHandler())
    queue._initialized = True
    ack = AsyncMock()
    monkeypatch.setattr(queue, "ack", ack)
    monkeypatch.setattr(
        queue,
        "_read_queue_message",
        AsyncMock(return_value={"id": "message-1", "data": {}}),
    )

    with pytest.raises(asyncio.CancelledError):
        await queue.dequeue()

    ack.assert_not_awaited()
    assert queue._in_progress == 0


@pytest.mark.asyncio
async def test_confirmed_task_cancellation_is_acknowledged(monkeypatch):
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("session_commit", account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)
    cleanup = AsyncMock(return_value=ProcessResult.cancelled())

    class CancelledHandler:
        async def on_dequeue(self, message):
            raise AssertionError("cancelled queued work must not enter the handler")

        async def on_discard(self, message, *, reason, handler_started):
            assert reason is DiscardReason.USER_CANCELLED
            assert handler_started is False
            return await cleanup(message)

    payload = await _middleware_enqueue(
        middleware,
        "Semantic",
        {"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )
    assert await tracker.cancel(task.task_id, account_id="a", user_id="u")

    queue = NamedQueue(
        None,
        "/queue",
        "Semantic",
        dequeue_handler=CancelledHandler(),
        middlewares=[middleware],
    )
    queue._initialized = True
    ack = AsyncMock()
    monkeypatch.setattr(queue, "ack", ack)
    monkeypatch.setattr(
        queue,
        "_read_queue_message",
        AsyncMock(return_value={"id": "message-1", "data": payload}),
    )

    assert await queue.dequeue() is None
    cleanup.assert_awaited_once()
    ack.assert_awaited_once()
    assert queue._in_progress == 0
    assert queue._processed == 1


@pytest.mark.asyncio
async def test_failed_user_cancellation_cleanup_is_not_acknowledged(monkeypatch):
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("session_commit", account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)

    class CancelledHandler:
        async def on_dequeue(self, message):
            raise AssertionError("cancelled queued work must not enter the handler")

        async def on_discard(self, message, *, reason, handler_started):
            assert reason is DiscardReason.USER_CANCELLED
            assert handler_started is False
            raise RuntimeError("cleanup failed")

    payload = await _middleware_enqueue(
        middleware,
        "Semantic",
        {"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )
    assert await tracker.cancel(task.task_id, account_id="a", user_id="u")

    queue = NamedQueue(
        None,
        "/queue",
        "Semantic",
        dequeue_handler=CancelledHandler(),
        middlewares=[middleware],
    )
    queue._initialized = True
    ack = AsyncMock()
    monkeypatch.setattr(queue, "ack", ack)
    monkeypatch.setattr(
        queue,
        "_read_queue_message",
        AsyncMock(return_value={"id": "message-1", "data": payload}),
    )

    assert await queue.dequeue() is None
    ack.assert_not_awaited()
    assert queue._in_progress == 0
    assert queue._error_count == 1


@pytest.mark.asyncio
async def test_persisted_user_cancellation_cleans_up_and_settles_cancelled_task():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("session_commit", account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)
    started = asyncio.Event()
    cleaned = asyncio.Event()

    class BlockingHandler:
        async def on_dequeue(self, message):
            started.set()
            await asyncio.Event().wait()

        async def on_discard(self, message, *, reason, handler_started):
            assert reason is DiscardReason.USER_CANCELLED
            assert handler_started is True
            cleaned.set()
            return ProcessResult.cancelled()

    payload = await _middleware_enqueue(
        middleware,
        "Semantic",
        {"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )
    queue = NamedQueue(
        None,
        "/queue",
        "Semantic",
        dequeue_handler=BlockingHandler(),
        middlewares=[middleware],
    )
    queue._initialized = True
    queue._read_queue_message = AsyncMock(return_value={"id": "message-1", "data": payload})
    queue.ack = AsyncMock()
    processing = asyncio.create_task(queue.consume_one())
    await asyncio.wait_for(started.wait(), timeout=1)

    assert await tracker.cancel(task.task_id, account_id="a", user_id="u")
    result = await asyncio.wait_for(processing, timeout=1)

    cancelled = await tracker.get(task.task_id, account_id="a", user_id="u")
    assert cleaned.is_set()
    assert result.outcome is ProcessOutcome.CANCELLED
    assert cancelled.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_shutdown_cancellation_keeps_durable_work_open():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("content_write", account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)
    started = asyncio.Event()

    class BlockingHandler:
        async def on_dequeue(self, message):
            started.set()
            await asyncio.Event().wait()

        async def on_discard(self, message, *, reason, handler_started):
            raise AssertionError("shutdown must not run business cancellation cleanup")

    payload = await _middleware_enqueue(
        middleware,
        "Semantic",
        {"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )
    queue = NamedQueue(
        None,
        "/queue",
        "Semantic",
        dequeue_handler=BlockingHandler(),
        middlewares=[middleware],
    )
    queue._initialized = True
    queue._read_queue_message = AsyncMock(return_value={"id": "message-1", "data": payload})
    queue.ack = AsyncMock()
    processing = asyncio.create_task(queue.consume_one())
    await asyncio.wait_for(started.wait(), timeout=1)

    processing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await processing

    running = await tracker.get(task.task_id, account_id="a", user_id="u")
    assert running.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
    assert await tracker.has_work(task.task_id, account_id="a", user_id="u")
    assert queue._in_progress == 0


@pytest.mark.asyncio
async def test_requeue_is_persisted_before_old_message_is_acknowledged(monkeypatch):
    events = []
    replacement = {"task_id": "task-1", "attempt": 2}

    class RetryHandler:
        async def on_dequeue(self, message):
            return ProcessResult.requeue(replacement, error="try again")

    queue = NamedQueue(None, "/queue", "Semantic", dequeue_handler=RetryHandler())
    queue._initialized = True
    monkeypatch.setattr(
        queue,
        "_read_queue_message",
        AsyncMock(return_value={"id": "message-1", "data": {}}),
    )

    async def enqueue_retry(payload, *, attempt):
        events.append(("retry", payload, attempt))
        return "message-2"

    async def ack(msg_id, message):
        events.append(("ack", msg_id))

    monkeypatch.setattr(queue, "enqueue_retry", enqueue_retry)
    monkeypatch.setattr(queue, "ack", ack)

    assert await queue.dequeue() is None
    assert events == [("retry", replacement, 1), ("ack", "message-1")]
    assert queue._in_progress == 0
    assert queue._processed == 1
    assert queue._requeue_count == 1


@pytest.mark.asyncio
async def test_retry_enqueue_always_assigns_a_new_work_id():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("add_resource", account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)
    source = await _middleware_enqueue(
        middleware,
        "Semantic",
        {"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )
    replacement = await _middleware_enqueue(
        middleware,
        "Semantic",
        source,
        kind=EnqueueKind.RETRY,
    )

    assert replacement[TASK_WORK_ID_FIELD] != source[TASK_WORK_ID_FIELD]
    assert (
        await tracker.get_work_state(
            task.task_id,
            source[TASK_WORK_ID_FIELD],
            account_id="a",
            user_id="u",
        )
        is WorkState.PENDING
    )
    assert (
        await tracker.get_work_state(
            task.task_id,
            replacement[TASK_WORK_ID_FIELD],
            account_id="a",
            user_id="u",
        )
        is WorkState.PENDING
    )


@pytest.mark.asyncio
async def test_terminal_work_redelivery_skips_handler():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("content_write", account_id="a", user_id="u")
    middleware = TaskWorkQueueMiddleware(tracker)
    payload = await _middleware_enqueue(
        middleware,
        "Semantic",
        {"task_id": task.task_id, "account_id": "a", "user_id": "u"},
    )
    await tracker.mark_work_done(
        task.task_id,
        payload[TASK_WORK_ID_FIELD],
        account_id="a",
        user_id="u",
    )

    class Handler:
        on_dequeue = AsyncMock(return_value=ProcessResult.success())

    queue = NamedQueue(
        None,
        "/queue",
        "Semantic",
        dequeue_handler=Handler(),
        middlewares=[middleware],
    )
    queue._initialized = True
    queue._read_queue_message = AsyncMock(return_value={"id": "message-1", "data": payload})
    queue.ack = AsyncMock()

    result = await queue.consume_one()

    assert result is not None
    assert result.outcome is ProcessOutcome.DUPLICATE
    Handler.on_dequeue.assert_not_awaited()
    queue.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_restore_preserves_terminal_work():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("add_resource", account_id="a", user_id="u")
    await tracker.register_work(
        task.task_id,
        "work-1",
        "AddResource",
        account_id="a",
        user_id="u",
    )
    await tracker.mark_work_done(
        task.task_id,
        "work-1",
        account_id="a",
        user_id="u",
    )

    await tracker.restore_work(
        task.task_id,
        "work-1",
        "AddResource",
        account_id="a",
        user_id="u",
    )

    assert (
        await tracker.get_work_state(
            task.task_id,
            "work-1",
            account_id="a",
            user_id="u",
        )
        is WorkState.DONE
    )


@pytest.mark.asyncio
async def test_terminal_task_with_missing_work_is_treated_as_duplicate():
    tracker = TaskTracker(CachingTaskWorkStore(PersistentTaskStore(_Agfs())))
    task = await tracker.create("content_write", account_id="a", user_id="u")
    await tracker.complete(
        task.task_id,
        {"ok": True},
        account_id="a",
        user_id="u",
    )
    middleware = TaskWorkQueueMiddleware(tracker)
    handler = AsyncMock(return_value=ProcessResult.success())
    message = {
        "task_id": task.task_id,
        TASK_WORK_ID_FIELD: "missing-work",
        TASK_ACCOUNT_ID_FIELD: "a",
        TASK_USER_ID_FIELD: "u",
    }

    result = await middleware.process(
        _process_context("Semantic", message),
        handler,
    )

    assert result.outcome is ProcessOutcome.DUPLICATE
    handler.assert_not_awaited()
    assert (
        await tracker.get_work_state(
            task.task_id,
            "missing-work",
            account_id="a",
            user_id="u",
        )
        is None
    )


@pytest.mark.asyncio
async def test_all_middleware_operations_use_onion_order(monkeypatch):
    events = []

    class RecordingMiddleware(QueueMiddleware):
        def __init__(self, name):
            self.name = name

        async def enqueue(self, ctx, call_next):
            events.append((self.name, "enqueue-before"))
            try:
                return await call_next(ctx)
            finally:
                events.append((self.name, "enqueue-after"))

        async def process(self, ctx, call_next):
            events.append((self.name, "process-before"))
            try:
                return await call_next(ctx)
            finally:
                events.append((self.name, "process-after"))

        async def ack(self, ctx, call_next):
            events.append((self.name, "ack-before"))
            try:
                await call_next(ctx)
            finally:
                events.append((self.name, "ack-after"))

    queue = NamedQueue(
        None,
        "/queue",
        "Semantic",
        middlewares=[RecordingMiddleware("outer"), RecordingMiddleware("inner")],
    )
    queue._initialized = True

    async def enqueue_core(ctx):
        events.append(("core", "enqueue"))
        ctx.committed_msg_id = "message-1"
        return "message-1"

    async def process_core(ctx):
        events.append(("core", "process"))
        return ProcessResult.success()

    async def ack_core(ctx):
        events.append(("core", "ack"))

    monkeypatch.setattr(queue, "_enqueue_core", enqueue_core)
    monkeypatch.setattr(queue, "_process_core", process_core)
    monkeypatch.setattr(queue, "_ack_core", ack_core)

    await queue.enqueue({})
    await queue._invoke_process({})
    await queue.ack("message-1", {})

    assert events == [
        ("outer", "enqueue-before"),
        ("inner", "enqueue-before"),
        ("core", "enqueue"),
        ("inner", "enqueue-after"),
        ("outer", "enqueue-after"),
        ("outer", "process-before"),
        ("inner", "process-before"),
        ("core", "process"),
        ("inner", "process-after"),
        ("outer", "process-after"),
        ("outer", "ack-before"),
        ("inner", "ack-before"),
        ("core", "ack"),
        ("inner", "ack-after"),
        ("outer", "ack-after"),
    ]


@pytest.mark.asyncio
async def test_retry_limit_becomes_failed_and_acknowledged():
    class RetryHandler:
        async def on_dequeue(self, message):
            del message
            return ProcessResult.requeue(
                {"task_id": "task-1"},
                error="still unavailable",
                max_attempts=3,
            )

    queue = NamedQueue(None, "/queue", "Semantic", dequeue_handler=RetryHandler())
    queue._initialized = True
    queue._read_queue_message = AsyncMock(
        return_value={
            "id": "message-3",
            "data": {"task_id": "task-1", "_queue_attempt": 2},
        }
    )
    queue.enqueue_retry = AsyncMock()
    queue.ack = AsyncMock()

    result = await queue.consume_one()

    assert result is not None
    assert result.outcome is ProcessOutcome.FAILED
    assert result.error == "still unavailable"
    queue.enqueue_retry.assert_not_awaited()
    queue.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_requeue_is_unbounded_for_transient_recovery():
    replacement = {"task_id": "task-1"}

    class RetryHandler:
        async def on_dequeue(self, message):
            del message
            return ProcessResult.requeue(replacement, error="service unavailable")

    queue = NamedQueue(None, "/queue", "Semantic", dequeue_handler=RetryHandler())
    queue._initialized = True
    queue._read_queue_message = AsyncMock(
        return_value={
            "id": "message-4",
            "data": {"task_id": "task-1", "_queue_attempt": 99},
        }
    )
    queue.enqueue_retry = AsyncMock(return_value="message-5")
    queue.ack = AsyncMock()

    result = await queue.consume_one()

    assert result is not None
    assert result.outcome is ProcessOutcome.REQUEUE
    queue.enqueue_retry.assert_awaited_once_with(replacement, attempt=100)
    queue.ack.assert_awaited_once()
