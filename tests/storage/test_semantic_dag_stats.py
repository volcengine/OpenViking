# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import TaskWorkIndex, TaskWorkRejected
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.semantic_dag import (
    DagStats,
    DagWork,
    SemanticDagExecutor,
    SemanticNodeScheduler,
)
from openviking_cli.session.user_id import UserIdentifier


class _FakeAgfs:
    async def pathlock_acquire_exact_batch(self, _paths):
        return {"lease_ref": "test"}

    async def pathlock_release(self, _lease):
        return None


class _FakeVikingFS:
    def __init__(self, tree):
        self._tree = tree
        self.writes = []
        self._async_agfs = _FakeAgfs()

    async def ls(self, uri, node_limit=None, ctx=None):
        del node_limit
        return self._tree.get(uri, [])

    async def write_file(
        self,
        path,
        content,
        ctx=None,
        lease_ref=None,
        lock_handle=None,
    ):
        del ctx, lease_ref, lock_handle
        self.writes.append((path, content))

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")


class _FakeProcessor:
    def __init__(self):
        self.vectorized_dirs = []
        self.vectorized_files = []

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        return {"name": file_path.split("/")[-1], "summary": "summary"}

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        return "overview"

    def _normalize_overview_generation(self, overview):
        return overview, "abstract"

    async def _vectorize_directory(
        self,
        uri,
        context_type,
        abstract,
        overview,
        ctx=None,
        semantic_msg_id=None,
        ingest_options=None,
    ):
        self.vectorized_dirs.append(uri)

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        semantic_msg_id=None,
        use_summary=False,
        ingest_options=None,
    ):
        self.vectorized_files.append(file_path)

    async def _vectorize_directory_simple(self, uri, context_type, abstract, overview, ctx=None):
        await self._vectorize_directory(uri, context_type, abstract, overview, ctx=ctx)


class _TrackingProcessor(_FakeProcessor):
    def __init__(self):
        super().__init__()
        self.active_summaries = 0
        self.max_active_summaries = 0

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        self.active_summaries += 1
        self.max_active_summaries = max(self.max_active_summaries, self.active_summaries)
        try:
            await asyncio.sleep(0.01)
            return {"name": file_path.split("/")[-1], "summary": "summary"}
        finally:
            self.active_summaries -= 1


class _DummyTracker:
    def __init__(self):
        self.register_calls = []

    async def register(self, **_kwargs):
        self.register_calls.append(_kwargs)
        return None


class _CompletingTracker:
    def __init__(self):
        self.remaining = 0
        self.total = 0
        self.on_complete = None
        self.completed = False
        self.active_id = None
        self.aborted_ids = []

    async def register(self, *, semantic_msg_id, total_count, on_complete, **_kwargs):
        self.active_id = semantic_msg_id
        self.remaining = total_count
        self.total = total_count
        self.on_complete = on_complete

    async def decrement(self, semantic_msg_id):
        if semantic_msg_id != self.active_id:
            return None
        self.remaining -= 1
        if self.remaining == 0:
            self.completed = True
            self.active_id = None
            await self.on_complete()
        return self.remaining

    async def abort(self, semantic_msg_id):
        if semantic_msg_id != self.active_id:
            return False
        self.aborted_ids.append(semantic_msg_id)
        self.active_id = None
        return True


class _FailingVectorizeProcessor(_FakeProcessor):
    def __init__(self, tracker):
        super().__init__()
        self._tracker = tracker

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        semantic_msg_id=None,
        use_summary=False,
        ingest_options=None,
    ):
        self.vectorized_files.append(file_path)
        try:
            raise RuntimeError("embedding enqueue unavailable")
        finally:
            await self._tracker.decrement(semantic_msg_id)

    async def _vectorize_directory(
        self,
        uri,
        context_type,
        abstract,
        overview,
        ctx=None,
        semantic_msg_id=None,
        ingest_options=None,
    ):
        self.vectorized_dirs.append(uri)
        await self._tracker.decrement(semantic_msg_id)
        await self._tracker.decrement(semantic_msg_id)


class _StalledEmbeddingProcessor(_FailingVectorizeProcessor):
    async def _vectorize_directory(
        self,
        uri,
        context_type,
        abstract,
        overview,
        ctx=None,
        semantic_msg_id=None,
        ingest_options=None,
    ):
        self.vectorized_dirs.append(uri)
        # Both directory messages were accepted, but the embedding worker is
        # unavailable and therefore never decrements their tracker slots.


@pytest.mark.asyncio
async def test_semantic_dag_propagates_vectorization_failure_after_tracker_drains(monkeypatch):
    root_uri = "viking://resources/root"
    tree = {root_uri: [{"name": "a.txt", "isDir": False}]}
    fake_fs = _FakeVikingFS(tree)
    tracker = _CompletingTracker()
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: tracker,
    )

    processor = _FailingVectorizeProcessor(tracker)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        semantic_msg_id="semantic-root",
    )

    with pytest.raises(RuntimeError, match="embedding enqueue unavailable"):
        await executor.run(root_uri)

    assert processor.vectorized_files == [f"{root_uri}/a.txt"]
    assert processor.vectorized_dirs == [root_uri]
    assert tracker.total == 3
    assert tracker.remaining == 0
    assert tracker.completed is True


@pytest.mark.asyncio
async def test_semantic_dag_aborts_stalled_embedding_attempt_after_dispatch_failure(monkeypatch):
    root_uri = "viking://resources/root"
    tree = {root_uri: [{"name": "a.txt", "isDir": False}]}
    fake_fs = _FakeVikingFS(tree)
    tracker = _CompletingTracker()
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: tracker,
    )

    processor = _StalledEmbeddingProcessor(tracker)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        semantic_msg_id="semantic-root",
    )

    with pytest.raises(RuntimeError, match="embedding enqueue unavailable"):
        await asyncio.wait_for(executor.run(root_uri), timeout=0.5)

    assert processor.vectorized_files == [f"{root_uri}/a.txt"]
    assert processor.vectorized_dirs == [root_uri]
    assert tracker.remaining == 2
    assert tracker.completed is False
    assert len(tracker.aborted_ids) == 1
    aborted_id = tracker.aborted_ids[0]
    assert aborted_id.startswith("semantic-root:")
    assert await tracker.decrement(aborted_id) is None


class _ScheduledExecutor:
    def __init__(self, run) -> None:
        self.closed = False
        self.failure = None
        self._run = run

    def _start_scheduled_work(self) -> None:
        return None

    def _finish_scheduled_work(self) -> None:
        return None

    async def _run_work(self, _work) -> None:
        await self._run()

    def fail(self, exc: Exception) -> None:
        self.failure = exc


@pytest.mark.asyncio
async def test_semantic_dag_stats_collects_nodes(monkeypatch):
    root_uri = "viking://resources/root"
    tree = {
        root_uri: [
            {"name": "a.txt", "isDir": False},
            {"name": "b.txt", "isDir": False},
            {"name": "child", "isDir": True},
        ],
        f"{root_uri}/child": [
            {"name": "c.txt", "isDir": False},
        ],
    }
    fake_fs = _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _DummyTracker(),
    )

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
    )
    await executor.run(root_uri)
    await asyncio.sleep(0)

    stats = executor.get_stats()
    assert isinstance(stats, DagStats)
    assert stats.total_nodes == 5  # 2 dirs + 3 files
    assert stats.pending_nodes == 0
    assert stats.done_nodes == 5
    assert stats.in_progress_nodes == 0
    assert processor.vectorized_dirs == [f"{root_uri}/child", root_uri]
    assert sorted(processor.vectorized_files) == sorted(
        [
            f"{root_uri}/a.txt",
            f"{root_uri}/b.txt",
            f"{root_uri}/child/c.txt",
        ]
    )


@pytest.mark.asyncio
async def test_semantic_dag_bounds_active_node_work(monkeypatch):
    root_uri = "viking://resources/root"
    tree = {
        root_uri: [{"name": f"file-{idx}.txt", "isDir": False} for idx in range(40)],
    }
    fake_fs = _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _DummyTracker(),
    )

    processor = _TrackingProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=3,
        ctx=ctx,
        skip_vectorization=True,
    )

    max_running = 0
    run_task = asyncio.create_task(executor.run(root_uri))
    while not run_task.done():
        max_running = max(max_running, executor.get_stats().in_progress_nodes)
        await asyncio.sleep(0)
    await run_task

    stats = executor.get_stats()
    assert stats.total_nodes == 41
    assert stats.done_nodes == 41
    assert stats.pending_nodes == 0
    assert stats.in_progress_nodes == 0
    assert processor.max_active_summaries <= 3
    assert max_running <= 3


@pytest.mark.asyncio
async def test_semantic_dag_shares_node_scheduler_across_roots(monkeypatch):
    root_a = "viking://resources/root-a"
    root_b = "viking://resources/root-b"
    tree = {
        root_a: [{"name": f"a-{idx}.txt", "isDir": False} for idx in range(20)],
        root_b: [{"name": f"b-{idx}.txt", "isDir": False} for idx in range(20)],
    }
    fake_fs = _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _DummyTracker(),
    )

    processor = _TrackingProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor_a = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=4,
        ctx=ctx,
        skip_vectorization=True,
    )
    executor_b = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=4,
        ctx=ctx,
        skip_vectorization=True,
    )

    await asyncio.gather(executor_a.run(root_a), executor_b.run(root_b))

    assert processor.max_active_summaries <= 4
    assert executor_a.get_stats().done_nodes == 21
    assert executor_b.get_stats().done_nodes == 21


@pytest.mark.asyncio
async def test_task_work_rejection_does_not_stop_shared_semantic_worker():
    work_index = TaskWorkIndex()

    async def finalize_before_ack(_metadata):
        return None

    work_index.set_callbacks(
        finalize_before_ack=finalize_before_ack,
        is_cancellation_requested=lambda _task_id: True,
    )
    embedding_queue = NamedQueue(
        None,
        "/queue",
        "Embedding",
        task_work_index=work_index,
    )
    embedding_queue._initialized = True
    unrelated_ran = asyncio.Event()

    async def rejected_work() -> None:
        await embedding_queue.enqueue(
            {
                "task_id": "task-a",
                "account_id": "account-a",
                "user_id": "user-a",
            }
        )

    async def unrelated_work() -> None:
        unrelated_ran.set()

    rejected = _ScheduledExecutor(rejected_work)
    unrelated = _ScheduledExecutor(unrelated_work)
    scheduler = SemanticNodeScheduler(max_workers=1)
    scheduler.submit(rejected, DagWork(kind="vectorize", dir_uri="a"))
    scheduler.submit(unrelated, DagWork(kind="vectorize", dir_uri="b"))

    await asyncio.wait_for(unrelated_ran.wait(), timeout=0.5)
    await asyncio.wait_for(scheduler._queue.join(), timeout=0.5)
    await asyncio.sleep(scheduler._idle_timeout * 2)

    assert isinstance(rejected.failure, TaskWorkRejected)
    assert unrelated.failure is None
    assert scheduler._queue.empty()
    assert all(worker.done() for worker in scheduler._workers)


@pytest.mark.asyncio
async def test_semantic_dag_skip_vectorization_does_not_schedule_tasks(monkeypatch):
    root_uri = "viking://resources/root"
    tree = {
        root_uri: [
            {"name": "a.txt", "isDir": False},
            {"name": "child", "isDir": True},
        ],
        f"{root_uri}/child": [
            {"name": "b.txt", "isDir": False},
        ],
    }
    fake_fs = _FakeVikingFS(tree)
    tracker = _DummyTracker()
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: tracker,
    )

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        skip_vectorization=True,
    )
    await executor.run(root_uri)
    await asyncio.sleep(0)

    assert fake_fs.writes == [
        (f"{root_uri}/child/.overview.md", "overview"),
        (f"{root_uri}/child/.abstract.md", "abstract"),
        (f"{root_uri}/.overview.md", "overview"),
        (f"{root_uri}/.abstract.md", "abstract"),
    ]
    assert processor.vectorized_dirs == []
    assert processor.vectorized_files == []
    assert tracker.register_calls == []


if __name__ == "__main__":
    pytest.main([__file__])
