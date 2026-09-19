# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_work_index import (
    TaskWorkIndex,
    bind_task_context,
    get_task_context,
    prepare_task_payload,
)
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_dag import DagStats, SemanticDagExecutor
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking_cli.session.user_id import UserIdentifier
from tests.storage.test_collection_schemas import _DummyConfig, _DummyEmbedder
from tests.storage.test_semantic_dag_skip_files import _FakeVikingFS


def _ctx():
    return RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)


def _processor():
    processor = SemanticProcessor()
    processor._generate_overview = AsyncMock(return_value="overview")
    processor._skill_root_semantics = AsyncMock(return_value=("overview", "abstract"))
    processor._vectorize_single_file = AsyncMock(return_value=True)
    processor._vectorize_directory = AsyncMock()
    return processor


@pytest.mark.asyncio
async def test_cancel_skill_stops_model_work_without_cancelling_other_packages(monkeypatch):
    root_a = "viking://agent/skills/a"
    root_b = "viking://agent/skills/b"
    resource = "viking://resources/document"
    fs = _FakeVikingFS(
        {
            root_a: [{"name": "active.md"}, {"name": "pending.md"}],
            root_b: [{"name": "b.md"}],
            resource: [{"name": "resource.md"}],
        }
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fs)
    processor = _processor()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    summaries = []
    owners = {}

    async def summarize(uri, **kwargs):
        summaries.append(uri)
        if uri == f"{root_a}/active.md":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return {"name": uri.rsplit("/", 1)[-1], "summary": "summary"}

    async def vectorize(**kwargs):
        owner = get_task_context()
        owners[kwargs["file_path"]] = owner.task_id if owner else None
        return True

    processor._generate_single_file_summary = summarize
    processor._vectorize_single_file = vectorize
    with bind_task_context("skill-a", "acc", "alice"):
        executor_a = SemanticDagExecutor(processor, "skill", 1, _ctx())
        task_a = asyncio.create_task(executor_a.run(root_a))
    await asyncio.wait_for(started.wait(), 1)
    with bind_task_context("skill-b", "acc", "alice"):
        executor_b = SemanticDagExecutor(processor, "skill", 1, _ctx())
        task_b = asyncio.create_task(executor_b.run(root_b))
    executor_resource = SemanticDagExecutor(processor, "resource", 1, _ctx())
    task_resource = asyncio.create_task(executor_resource.run(resource))
    task_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task_a, 1)
    await asyncio.wait_for(asyncio.gather(task_b, task_resource), 1)
    assert cancelled.is_set()
    assert f"{root_a}/pending.md" not in summaries
    assert owners == {f"{root_b}/b.md": "skill-b", f"{resource}/resource.md": None}
    assert not any(uri.startswith(root_a) for uri, _content in fs.writes)


@pytest.mark.asyncio
async def test_cancel_skill_waits_for_started_threaded_sidecar_write(monkeypatch):
    root = "viking://agent/skills/demo/reference"
    fs = _FakeVikingFS({root: []})
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fs)
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def write_in_thread(path, content):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(3)
        fs.writes.append((path, content))

    async def write(path, content, **kwargs):
        await asyncio.to_thread(write_in_thread, path, content)

    fs.write_file = write
    processor = _processor()
    executor = SemanticDagExecutor(processor, "skill", 1, _ctx())
    worker = asyncio.create_task(executor.run(root))
    try:
        await asyncio.wait_for(started.wait(), 1)
        worker.cancel()
        await asyncio.sleep(0)
        worker.cancel()
        await asyncio.sleep(0.03)
        assert not worker.done()
        assert not fs.writes
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 1)
    assert fs.writes
    writes_at_exit = list(fs.writes)
    await asyncio.sleep(0.03)
    assert fs.writes == writes_at_exit
    processor._vectorize_directory.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("during_write", [False, True])
async def test_active_skill_embedding_cancel_settles_only_after_write_exit(
    monkeypatch, during_write
):
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    writes = []
    model_cancelled = asyncio.Event()

    class Embedder(_DummyEmbedder):
        async def embed_async(self, text, is_query=False):
            if not during_write:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    model_cancelled.set()
            return EmbedResult(dense_vector=[0.1, 0.2])

    def write_in_thread(data):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(3)
        writes.append(data["uri"])
        return "vector-id"

    async def upsert(data, **kwargs):
        return await asyncio.to_thread(write_in_thread, data)

    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config", lambda: _DummyConfig(Embedder())
    )
    handler = TextEmbeddingHandler(
        SimpleNamespace(is_closing=False, uses_content_field=False, upsert=upsert)
    )
    telemetry_id = str(uuid4())
    tracker = get_request_wait_tracker()
    tracker.register_request(telemetry_id)
    msg = EmbeddingMsg(
        "content",
        {"uri": "viking://agent/skills/demo/file.md", "context_type": "skill"},
        telemetry_id=telemetry_id,
    )
    tracker.register_embedding_root(telemetry_id, msg.id)
    index = TaskWorkIndex()
    cancelling = set()
    index.set_callbacks(
        finalize_before_ack=AsyncMock(),
        is_cancellation_requested=lambda task_id: task_id in cancelling,
    )
    with bind_task_context("skill-task", "acc", "alice"):
        payload, metadata = prepare_task_payload(msg.to_dict())
    queue = NamedQueue(
        object(),
        "/queue",
        "Embedding",
        dequeue_handler=handler,
        middlewares=[TaskWorkQueueMiddleware(index)],
    )
    envelope = {"data": json.dumps(payload)}
    assert index.register(queue.name, metadata)
    worker = asyncio.create_task(queue.process_dequeued(envelope))
    try:
        await asyncio.wait_for(started.wait(), 1)
        cancelling.add("skill-task")
        index.cancel_active("skill-task")
        await asyncio.sleep(0.03)
        if during_write:
            assert not worker.done()
            assert not tracker.is_complete(telemetry_id)
            assert index.has_work("skill-task")
            assert not writes
        else:
            await asyncio.wait_for(worker, 1)
            assert model_cancelled.is_set()
    finally:
        release.set()
        result = await asyncio.wait_for(worker, 1)
        assert result.outcome is ProcessOutcome.CANCELLED
        await index.prepare_ack(queue.name, envelope)
        assert not index.has_work("skill-task")
        assert tracker.is_complete(telemetry_id)
        tracker.cleanup(telemetry_id)
    assert len(writes) == int(during_write)


@pytest.mark.asyncio
async def test_semantic_cancel_drains_embeddings_before_releasing_package(monkeypatch):
    tracker = get_request_wait_tracker()
    telemetry_id = str(uuid4())
    tracker.register_request(telemetry_id)
    msg = SemanticMsg(
        uri="viking://agent/skills/demo",
        context_type="skill",
        telemetry_id=telemetry_id,
        propagate_to_parent=False,
    )
    tracker.register_semantic_root(telemetry_id, msg.id)
    started = asyncio.Event()
    events = []

    class Executor:
        def __init__(self, **kwargs):
            self.stale = False

        async def run(self, uri):
            tracker.register_embedding_root(telemetry_id, "embedding")
            started.set()

        def get_stats(self):
            return DagStats()

    class Lease:
        lock = {"lease_ref": "skill"}

        async def close(self):
            events.append("released")

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor", Executor
    )
    monkeypatch.setattr(
        SemanticProcessor, "_resolve_skill_semantic_lock", AsyncMock(return_value=Lease())
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: SimpleNamespace(exists=AsyncMock(return_value=True)),
    )
    worker = asyncio.create_task(SemanticProcessor().on_dequeue({"data": msg.to_json()}))
    await asyncio.wait_for(started.wait(), 1)
    worker.cancel()
    await asyncio.sleep(0)
    worker.cancel()
    await asyncio.sleep(0.03)
    assert not worker.done()
    assert events == []
    assert not tracker.is_complete(telemetry_id)
    events.append("embedding-settled")
    tracker.mark_embedding_done(telemetry_id, "embedding")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(worker, 1)
    assert events == ["embedding-settled", "released"]
    assert tracker.is_complete(telemetry_id)
    tracker.cleanup(telemetry_id)
