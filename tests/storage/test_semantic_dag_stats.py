# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import (
    TaskWorkIndex,
    TaskWorkRejected,
    bind_task_context,
    get_task_context,
)
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.semantic_dag import (
    DagStats,
    DagWork,
    SemanticDagExecutor,
    SemanticNodeScheduler,
)
from openviking.telemetry import (
    OperationTelemetry,
    bind_telemetry,
    get_current_telemetry,
)
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, tree):
        self._tree = tree
        self.writes = []
        self._async_agfs = self

    async def ls(self, uri, node_limit=None, ctx=None):
        del node_limit
        return self._tree.get(uri, [])

    async def write_file(self, path, content, ctx=None, lease_ref=None):
        self.writes.append((path, content))

    async def pathlock_acquire_exact_batch(self, paths):
        return {"paths": paths}

    async def pathlock_release(self, lease):
        return None

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")


class _FakeProcessor:
    def __init__(self, verify_streaming=False):
        self.vectorized_dirs = []
        self.vectorized_files = []
        self.vectorized_contexts = {}
        self.verify_streaming = verify_streaming

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        result = {"name": file_path.split("/")[-1], "summary": "summary"}
        if self.verify_streaming:
            result["content"] = "x" * 100_000
        return result

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        if self.verify_streaming:
            assert all("content" not in item for item in file_summaries)
            assert all(
                f"{dir_uri}/{item['name']}" in self.vectorized_files for item in file_summaries
            )
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
        use_summary=False,
        ingest_options=None,
    ):
        if self.verify_streaming:
            assert summary_dict["content"]
        self.vectorized_files.append(file_path)
        task_context = get_task_context()
        self.vectorized_contexts[file_path] = (
            task_context.task_id if task_context is not None else None,
            get_current_telemetry().telemetry_id,
        )

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

    processor = _FakeProcessor(verify_streaming=True)
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

    processor = _TrackingProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    telemetry_a = OperationTelemetry("semantic-a")
    telemetry_b = OperationTelemetry("semantic-b")
    with (
        bind_task_context("task-a", "acc1", "user1"),
        bind_telemetry(telemetry_a),
    ):
        executor_a = SemanticDagExecutor(
            processor=processor,
            context_type="resource",
            max_concurrent_llm=1,
            ctx=ctx,
        )
    with (
        bind_task_context("task-b", "acc1", "user1"),
        bind_telemetry(telemetry_b),
    ):
        executor_b = SemanticDagExecutor(
            processor=processor,
            context_type="resource",
            max_concurrent_llm=1,
            ctx=ctx,
        )

    await asyncio.gather(executor_a.run(root_a), executor_b.run(root_b))

    assert processor.max_active_summaries == 1
    assert executor_a.get_stats().done_nodes == 21
    assert executor_b.get_stats().done_nodes == 21
    assert {
        processor.vectorized_contexts[f"{root_a}/a-{idx}.txt"] for idx in range(20)
    } == {("task-a", telemetry_a.telemetry_id)}
    assert {
        processor.vectorized_contexts[f"{root_b}/b-{idx}.txt"] for idx in range(20)
    } == {("task-b", telemetry_b.telemetry_id)}


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
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

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
if __name__ == "__main__":
    pytest.main([__file__])
