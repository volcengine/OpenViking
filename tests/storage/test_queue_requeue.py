# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise replacement writes, restart recovery and business failure cleanup."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.service.task_work_index import bind_task_context
from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult
from openviking.storage.queuefs.requeue import (
    REQUEUE_COUNT_FIELD,
    RequeueEnqueueError,
    RequeueLimitExceeded,
)
from tests.test_task_tracker import _FakeAgfs

OWNER = {"account_id": "account", "user_id": "user"}


@pytest.fixture
def queue_transport(monkeypatch):
    pending = []
    writes = []
    transport = AsyncMock(spec=AsyncAGFSClient)

    async def write(path, data):
        if path.endswith("/enqueue"):
            message = {"id": str(len(writes) + 1), "data": data.decode()}
            writes.append(message)
            pending.append(message)
            return message["id"]
        return ""

    transport.write.side_effect = write
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda _: transport
    )
    return SimpleNamespace(transport=transport, pending=pending, writes=writes)


class RetryingHandler(DequeueHandlerBase):
    def __init__(self, queue):
        self.queue = queue
        self.executions = 0
        self.exhausted = 0
        self.cancelled = 0

    async def on_dequeue(self, data):
        self.executions += 1
        payload = json.loads(data["data"])
        if payload["remaining"]:
            # Deliberately drop transport metadata; requeue must carry it itself.
            replacement = {"remaining": payload["remaining"] - 1}
            if payload.get("task_id"):
                replacement.update(
                    task_id=payload["task_id"], _task_work_id=payload["_task_work_id"]
                )
            await self.queue.requeue(replacement)
            return ProcessResult.requeued()
        return ProcessResult.success()

    async def on_requeue_exhausted(self, data, error):
        self.exhausted += 1
        return ProcessResult.failed(str(error))

    async def on_cancelled(self, data):
        self.cancelled += 1
        return ProcessResult.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit,retries,expected", [(-1, 3, 3), (-2, 3, 3), (0, 3, 0), (1, 3, 1), (3, 3, 3)]
)
async def test_requeue_cap_applies_before_write(queue_transport, limit, retries, expected):
    queue = NamedQueue(object(), "/queue", "Test", max_requeues=limit)
    handler = RetryingHandler(queue)
    queue.set_dequeue_handler(handler)
    await queue.enqueue({"remaining": retries})
    results = []
    while queue_transport.pending:
        results.append(await queue.process_dequeued(queue_transport.pending.pop(0)))
    assert len(queue_transport.writes) - 1 == expected
    assert handler.executions == expected + 1
    assert handler.cancelled == 0
    assert results[-1].outcome is (
        ProcessOutcome.SUCCESS if expected == retries else ProcessOutcome.FAILED
    )
    counts = [json.loads(m["data"])[REQUEUE_COUNT_FIELD] for m in queue_transport.writes[1:]]
    assert counts == list(range(1, expected + 1))


@pytest.mark.asyncio
async def test_zero_allows_work_that_never_requeues(queue_transport):
    queue = NamedQueue(object(), "/queue", "Test", max_requeues=0)
    handler = RetryingHandler(queue)
    queue.set_dequeue_handler(handler)
    await queue.enqueue({"remaining": 0})
    result = await queue.process_dequeued(queue_transport.pending.pop(0))
    assert result.outcome is ProcessOutcome.SUCCESS
    assert handler.executions == 1
    assert handler.exhausted == 0


@pytest.mark.asyncio
async def test_queue_recreation_preserves_retry_count(queue_transport):
    queue = NamedQueue(object(), "/queue", "Test", max_requeues=1)
    queue.set_dequeue_handler(RetryingHandler(queue))
    await queue.enqueue({"remaining": 3})
    await queue.process_dequeued(queue_transport.pending.pop(0))
    # A fresh consumer reads only the persisted replacement, with no Work store.
    restarted = NamedQueue(object(), "/queue", "Test", max_requeues=1)
    handler = RetryingHandler(restarted)
    restarted.set_dequeue_handler(handler)
    result = await restarted.process_dequeued(queue_transport.pending.pop(0))
    assert result.outcome is ProcessOutcome.FAILED
    assert len(queue_transport.writes) == 2
    assert handler.executions == 1


@pytest.mark.asyncio
async def test_enqueue_failure_keeps_original_delivery_unacknowledged(queue_transport):
    queue = NamedQueue(object(), "/queue", "Test", max_requeues=1)
    handler = RetryingHandler(queue)
    queue.set_dequeue_handler(handler)
    await queue.enqueue({"remaining": 1})
    message = queue_transport.pending.pop(0)
    original_write = queue_transport.transport.write.side_effect
    queue_transport.transport.read.return_value = json.dumps(message).encode()
    queue_transport.transport.write.reset_mock()
    queue_transport.transport.write.side_effect = RuntimeError("storage unavailable")
    await queue.dequeue()
    assert all(
        call.args[0].endswith("/enqueue")
        for call in queue_transport.transport.write.await_args_list
    )
    assert len(queue_transport.writes) == 1
    assert handler.exhausted == 0
    # Retry the original after transport recovery; a failed enqueue spent no budget.
    queue_transport.transport.write.side_effect = original_write
    result = await queue.process_dequeued(message)
    assert result.outcome is ProcessOutcome.REQUEUED
    assert json.loads(queue_transport.writes[-1]["data"])[REQUEUE_COUNT_FIELD] == 1


@pytest.mark.asyncio
async def test_counter_store_failure_and_restart_do_not_reset_limit(queue_transport, monkeypatch):
    agfs = _FakeAgfs()
    tracker = TaskTracker(PersistentTaskStore(agfs))
    set_task_tracker(tracker)
    try:
        await tracker.create("add_resource", task_id="task", **OWNER)
        await tracker.start("task", **OWNER)
        queue = NamedQueue(
            object(), "/queue", "Test", max_requeues=1, middlewares=[TaskWorkQueueMiddleware()]
        )
        queue.set_dequeue_handler(RetryingHandler(queue))
        with bind_task_context("task", **OWNER):
            await queue.enqueue({"remaining": 3, "_task_work_id": "work"})
        monkeypatch.setattr(
            tracker,
            "mark_work_requeued",
            AsyncMock(side_effect=RuntimeError("counter write failed")),
        )
        with pytest.raises(RuntimeError, match="counter write failed"):
            await queue.process_dequeued(queue_transport.pending.pop(0))
        restarted_tracker = TaskTracker(PersistentTaskStore(agfs))
        set_task_tracker(restarted_tracker)
        result = await queue.process_dequeued(queue_transport.pending.pop(0))
        assert result.outcome is ProcessOutcome.FAILED
        assert len(queue_transport.writes) == 2
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_child_enqueue_does_not_inherit_retry_budget(queue_transport):
    queue = NamedQueue(object(), "/queue", "Test", max_requeues=2)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            await queue.enqueue({"child": True})
            await queue.requeue({"replacement": True})
            return ProcessResult.requeued()

    queue.set_dequeue_handler(Handler())
    await queue.process_dequeued({"id": "source", "data": {REQUEUE_COUNT_FIELD: 1}})
    assert REQUEUE_COUNT_FIELD not in json.loads(queue_transport.writes[0]["data"])
    assert json.loads(queue_transport.writes[1]["data"])[REQUEUE_COUNT_FIELD] == 2


@pytest.mark.asyncio
async def test_legacy_handoff_counter_clamps_total(queue_transport):
    queue = NamedQueue(object(), "/queue", "Test", max_requeues=3)
    with pytest.raises(RequeueLimitExceeded):
        await queue.requeue({}, source={"lock_handoff_retry": 3})
    assert not queue_transport.writes


@pytest.mark.asyncio
async def test_uncommitted_callback_cannot_ack_source(queue_transport):
    queue = NamedQueue(object(), "/queue", "Test", max_requeues=1)
    with pytest.raises(RequeueEnqueueError):
        await queue.requeue({}, enqueue=AsyncMock(return_value=False))
    assert not queue_transport.writes


@pytest.mark.asyncio
async def test_external_exhaustion_is_failed_not_cancelled(queue_transport, monkeypatch):
    from openviking.service.external_task_service import ExternalTaskService
    from openviking.storage.queuefs.external_task_processor import ExternalTaskProcessor
    from openviking.storage.queuefs.queue_manager import QueueManager

    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    try:
        await tracker.create("test_external", task_id="task", meta={"request": {}}, **OWNER)
        await tracker.start("task", stage="queued", **OWNER)
        service = ExternalTaskService()
        service._providers["test_external"] = SimpleNamespace(
            serialization_key=lambda _: None, poll_interval_seconds=0.001
        )
        # Defer admission without calling any external service.
        service.execute = AsyncMock(return_value=False)
        manager = QueueManager(object(), max_requeues=0)
        queue = manager.get_queue("ExternalTask", ExternalTaskProcessor(service), allow_create=True)
        monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
        monkeypatch.setattr(
            "openviking.storage.queuefs.external_task_processor.get_queue_manager", lambda: manager
        )
        with bind_task_context("task", **OWNER):
            await queue.enqueue({"task_id": "task", **OWNER})
        result = await queue.process_dequeued(queue_transport.pending.pop(0))
        task = await tracker.get("task", **OWNER)
        assert result.outcome is ProcessOutcome.FAILED
        assert task.status.value == "failed"
        assert "requeue limit" in task.error
        assert len(queue_transport.writes) == 1
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_add_resource_exhaustion_cleans_source_and_records_failed(queue_transport):
    from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
    from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor

    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    try:
        await tracker.create("add_resource", task_id="task", **OWNER)
        await tracker.start("task", **OWNER)
        fs = SimpleNamespace(delete_temp=AsyncMock(), _async_agfs=SimpleNamespace())
        service = SimpleNamespace(record_watch_execution=AsyncMock())
        processor = AddResourceProcessor(service, "AddResource", fs)
        queue = NamedQueue(
            object(),
            "/queue",
            "AddResource",
            max_requeues=0,
            middlewares=[TaskWorkQueueMiddleware()],
            dequeue_handler=processor,
        )
        msg = AddResourceMsg(
            task_id="task",
            root_uri="viking://resources/test",
            role="user",
            **OWNER,
            path="/tmp/input.txt",
            watch_task_id="watch",
            staged_source={
                "temp_uri": "viking://temp/source",
                "source_uri": "viking://temp/source/source/input.txt",
                "source_type": "local",
                "original_source": "/tmp/input.txt",
                "meta": {},
            },
        )

        # Drive the real exhaustion cleanup through NamedQueue's handler boundary.
        async def blocked(data):
            await queue.requeue(msg.to_dict())
            return ProcessResult.requeued()

        processor.on_dequeue = blocked
        with bind_task_context("task", **OWNER):
            await queue.enqueue(msg.to_dict())
        result = await queue.process_dequeued(queue_transport.pending.pop(0))
        assert result.outcome is ProcessOutcome.FAILED
        fs.delete_temp.assert_awaited_once()
        assert service.record_watch_execution.await_args.kwargs["status"] == "failed"
        assert "requeue limit" in service.record_watch_execution.await_args.kwargs["error"]
        assert len(queue_transport.writes) == 1
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_session_exhaustion_writes_failure_marker(queue_transport, monkeypatch):
    from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
    from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor

    session = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        resume_queued_commit=AsyncMock(return_value=False),
        finalize_failed_commit=AsyncMock(),
        finalize_cancelled_commit=AsyncMock(),
    )
    processor = SessionCommitProcessor(SimpleNamespace(session=lambda *args, **kwargs: session))
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    try:
        await tracker.create("session_commit", task_id="task", **OWNER)
        await tracker.start("task", **OWNER)
        queue = NamedQueue(
            object(),
            "/queue",
            "SessionCommit",
            max_requeues=0,
            middlewares=[TaskWorkQueueMiddleware()],
            dequeue_handler=processor,
        )

        async def requeue(name, data):
            return await queue.requeue(data)

        manager = SimpleNamespace(SESSION_COMMIT="SessionCommit", requeue=requeue)
        monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
        msg = SessionCommitMsg(
            task_id="task",
            session_id="session",
            session_uri="viking://session/test",
            archive_uri="viking://session/test/history/archive_001",
            user=OWNER,
        )
        with bind_task_context("task", **OWNER):
            await queue.enqueue(msg.to_dict())
        result = await queue.process_dequeued(queue_transport.pending.pop(0))
        assert result.outcome is ProcessOutcome.FAILED
        session.finalize_failed_commit.assert_awaited_once()
        session.finalize_cancelled_commit.assert_not_awaited()
        assert len(queue_transport.writes) == 1
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_concurrent_deliveries_keep_separate_counts(queue_transport):
    queue = NamedQueue(object(), "/queue", "Test", max_requeues=5)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            await asyncio.sleep(0)
            await queue.requeue({"replacement": data["id"]})
            return ProcessResult.requeued()

    queue.set_dequeue_handler(Handler())
    await asyncio.gather(
        queue.process_dequeued({"id": "first", "data": {REQUEUE_COUNT_FIELD: 1}}),
        queue.process_dequeued({"id": "second", "data": {REQUEUE_COUNT_FIELD: 3}}),
    )
    assert sorted(json.loads(m["data"])[REQUEUE_COUNT_FIELD] for m in queue_transport.writes) == [
        2,
        4,
    ]


@pytest.mark.asyncio
async def test_semantic_retry_survives_serialization_and_dedup_window(queue_transport):
    from openviking.storage.queuefs.semantic_msg import SemanticMsg
    from openviking.storage.queuefs.semantic_queue import SemanticQueue

    queue = SemanticQueue(object(), "/queue", "Semantic", max_requeues=1)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            msg = SemanticMsg.from_dict(json.loads(data["data"]))
            await queue.requeue(msg)
            return ProcessResult.requeued()

    queue.set_dequeue_handler(Handler())
    await queue.enqueue(SemanticMsg(uri="viking://user/user/memories/test", context_type="memory"))
    result = await queue.process_dequeued(queue_transport.pending.pop(0))
    assert result.outcome is ProcessOutcome.REQUEUED
    result = await queue.process_dequeued(queue_transport.pending.pop(0))
    assert result.outcome is ProcessOutcome.FAILED
    assert len(queue_transport.writes) == 2


@pytest.mark.asyncio
async def test_sqlite_requeue_count_survives_native_restart(tmp_path):
    from openviking.pyagfs import AsyncAGFSClient
    from openviking.storage.queuefs.queue_manager import QueueManager
    from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
    from openviking_cli.utils.config.agfs_config import AGFSConfig

    config = RagfsBindingConfig(
        agfs=AGFSConfig(
            path=str(tmp_path),
            backend="local",
            queuefs={"backend": "sqlite", "recover_stale_sec": 0},
        )
    )

    def open_queue():
        client = create_agfs_client(config)
        manager = QueueManager(client, max_requeues=1)
        queue = manager.get_queue("Test", allow_create=True)
        queue.set_dequeue_handler(RetryingHandler(queue))
        return client, queue

    client, queue = open_queue()
    try:
        await queue.enqueue({"remaining": 3})
        await queue.dequeue()
        assert json.loads((await queue.snapshot())[0]["data"])[REQUEUE_COUNT_FIELD] == 1
    finally:
        await AsyncAGFSClient(client).run("close")

    client, restarted = open_queue()
    try:
        await restarted.dequeue()
        assert await restarted.snapshot() == []
        assert (await restarted.get_status()).error_count == 1
    finally:
        await AsyncAGFSClient(client).run("close")


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 1])
async def test_semantic_breaker_requeues_respect_cap(queue_transport, monkeypatch, limit):
    from openviking.storage.queuefs.semantic_msg import SemanticMsg
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor
    from openviking.storage.queuefs.semantic_queue import SemanticQueue
    from openviking.utils.circuit_breaker import CircuitBreakerOpen

    queue = SemanticQueue(object(), "/queue", "Semantic", max_requeues=limit)
    processor = SemanticProcessor()
    processor._account_breaker = lambda _: SimpleNamespace(
        check=lambda: (_ for _ in ()).throw(CircuitBreakerOpen("open")), retry_after=0
    )
    queue.set_dequeue_handler(processor)
    manager = SimpleNamespace(SEMANTIC="Semantic", get_queue=lambda _: queue)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    await queue.enqueue(SemanticMsg(uri="viking://resources/test", context_type="resource"))
    while queue_transport.pending:
        result = await queue.process_dequeued(queue_transport.pending.pop(0))
    assert result.outcome is ProcessOutcome.FAILED
    assert len(queue_transport.writes) == limit + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 1])
async def test_embedding_breaker_requeues_respect_cap(queue_transport, limit):
    from openviking.storage.collection_schemas import TextEmbeddingHandler
    from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
    from openviking.storage.queuefs.embedding_queue import EmbeddingQueue
    from openviking.utils.circuit_breaker import CircuitBreakerOpen

    queue = EmbeddingQueue(object(), "/queue", "Embedding", max_requeues=limit)
    embedder = SimpleNamespace(
        prepare_embedding_input=lambda content: content,
        embed_async=AsyncMock(side_effect=CircuitBreakerOpen("open")),
    )
    provider = SimpleNamespace(bind=lambda _: embedder)
    vikingdb = SimpleNamespace(embedding_queue=queue, is_closing=False, has_queue_manager=True)
    queue.set_dequeue_handler(TextEmbeddingHandler(vikingdb, provider))
    await queue.enqueue(
        EmbeddingMsg(
            message="test",
            context_data={
                "uri": "viking://resources/test",
                "account_id": "account",
                "context_type": "resource",
            },
        )
    )
    while queue_transport.pending:
        result = await queue.process_dequeued(queue_transport.pending.pop(0))
    assert result.outcome is ProcessOutcome.FAILED
    assert len(queue_transport.writes) == limit + 1
    assert embedder.embed_async.await_count == limit + 1
