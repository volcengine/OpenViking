# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.embedding_tracker import EmbeddingTaskTracker
from openviking.storage.queuefs.semantic_dag import DagStats, SemanticDagExecutor
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import RequestWaitTracker
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, tree):
        self._tree = tree
        self.writes = []

    async def ls(self, uri, node_limit=None, ctx=None):
        del node_limit
        return self._tree.get(uri, [])

    async def write_file(self, path, content, ctx=None):
        self.writes.append((path, content))

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")


class _FailingListVikingFS(_FakeVikingFS):
    async def ls(self, uri, node_limit=None, ctx=None):
        del uri, node_limit, ctx
        raise RuntimeError("directory listing unavailable")


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
        self, uri, context_type, abstract, overview, ctx=None, semantic_msg_id=None
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
        record_failure=True,
    ):
        self.vectorized_files.append(file_path)
        return True

    async def _vectorize_directory_simple(self, uri, context_type, abstract, overview, ctx=None):
        await self._vectorize_directory(uri, context_type, abstract, overview, ctx=ctx)

    async def _patch_file_summary(
        self,
        file_path,
        summary,
        ctx=None,
        semantic_msg_id=None,
    ):
        return True


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


class _StreamingTracker:
    def __init__(self, events):
        self.events = events

    async def register(self, **_kwargs):
        self.events.append("register")

    async def register_open(self, **_kwargs):
        self.events.append("register_open")

    async def add(self, _semantic_msg_id, count, leaf_count=0):
        self.events.append(f"add:{count}:{leaf_count}")

    async def seal_leaf(self, _semantic_msg_id):
        self.events.append("seal_leaf")

    async def seal(self, _semantic_msg_id):
        self.events.append("seal")

    async def discard(self, _semantic_msg_id):
        self.events.append("discard")


class _CompensatingStreamingTracker(_StreamingTracker):
    async def decrement(self, _semantic_msg_id, is_leaf=False):
        self.events.append(f"decrement:{is_leaf}")
        return 0


class _StreamingProcessor(_FakeProcessor):
    def __init__(self, events):
        super().__init__()
        self.events = events

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        self.events.append(f"summary:{file_path}")
        return await super()._generate_single_file_summary(file_path, llm_sem=llm_sem, ctx=ctx)

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        self.events.append(f"overview:{dir_uri}")
        return await super()._generate_overview(dir_uri, file_summaries, children_abstracts)

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        semantic_msg_id=None,
        use_summary=False,
        record_failure=True,
    ):
        self.events.append(f"vectorize:{file_path}")
        await super()._vectorize_single_file(
            parent_uri,
            context_type,
            file_path,
            summary_dict,
            ctx=ctx,
            semantic_msg_id=semantic_msg_id,
            use_summary=use_summary,
        )
        return True


class _EmbeddingFirstProcessor(_FakeProcessor):
    def __init__(self):
        super().__init__()
        self.events = []
        self.summary_started = asyncio.Event()
        self.allow_summary = asyncio.Event()
        self.vectorize_summaries = []
        self.metadata_patches = []

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        self.events.append("summary_started")
        self.summary_started.set()
        await self.allow_summary.wait()
        return {"name": file_path.split("/")[-1], "summary": "generated summary"}

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        semantic_msg_id=None,
        use_summary=False,
        record_failure=True,
    ):
        self.events.append("vectorize_persisted")
        self.vectorize_summaries.append(dict(summary_dict))
        return True

    async def _patch_file_summary(
        self,
        file_path,
        summary,
        ctx=None,
        semantic_msg_id=None,
    ):
        self.metadata_patches.append((file_path, summary, semantic_msg_id))
        return True


class _EmbeddingFirstCompletingProcessor(_EmbeddingFirstProcessor):
    def __init__(self, embedding_tracker):
        super().__init__()
        self.embedding_tracker = embedding_tracker

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        semantic_msg_id=None,
        use_summary=False,
        record_failure=True,
    ):
        await super()._vectorize_single_file(
            parent_uri,
            context_type,
            file_path,
            summary_dict,
            ctx=ctx,
            semantic_msg_id=semantic_msg_id,
            use_summary=use_summary,
        )
        await self.embedding_tracker.decrement(semantic_msg_id, is_leaf=True)
        return True


class _MetadataPatchRejectingProcessor(_EmbeddingFirstProcessor):
    async def _patch_file_summary(
        self,
        file_path,
        summary,
        ctx=None,
        semantic_msg_id=None,
    ):
        return False


class _EmbeddingFirstFallbackProcessor(_EmbeddingFirstProcessor):
    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        semantic_msg_id=None,
        use_summary=False,
        record_failure=True,
    ):
        self.events.append("vectorize_attempt")
        self.vectorize_summaries.append(dict(summary_dict))
        return bool(summary_dict.get("summary"))


class _MixedLeafProcessor(_FakeProcessor):
    def __init__(self, embedding_tracker):
        super().__init__()
        self.embedding_tracker = embedding_tracker
        self.media_summary_started = asyncio.Event()
        self.allow_media_summary = asyncio.Event()

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        if file_path.endswith(".png"):
            self.media_summary_started.set()
            await self.allow_media_summary.wait()
        return {"name": file_path.split("/")[-1], "summary": "summary"}

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        semantic_msg_id=None,
        use_summary=False,
        record_failure=True,
    ):
        await self.embedding_tracker.decrement(semantic_msg_id, is_leaf=True)
        return True

    async def _patch_file_summary(
        self,
        file_path,
        summary,
        ctx=None,
        semantic_msg_id=None,
    ):
        await self.embedding_tracker.decrement(semantic_msg_id, is_leaf=False)
        return True


class _CompletingEmbeddingProcessor(_FakeProcessor):
    def __init__(self, embedding_tracker, request_tracker, telemetry_id):
        super().__init__()
        self.embedding_tracker = embedding_tracker
        self.request_tracker = request_tracker
        self.telemetry_id = telemetry_id
        self.leaf_indexed_at_overview = {}
        self.vectorize_telemetry_ids = []

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        self.leaf_indexed_at_overview[dir_uri] = self.request_tracker.is_leaf_indexed(
            self.telemetry_id
        )
        return await super()._generate_overview(dir_uri, file_summaries, children_abstracts)

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        semantic_msg_id=None,
        use_summary=False,
        record_failure=True,
    ):
        self.vectorize_telemetry_ids.append(get_current_telemetry().telemetry_id)
        await super()._vectorize_single_file(
            parent_uri,
            context_type,
            file_path,
            summary_dict,
            ctx=ctx,
            semantic_msg_id=semantic_msg_id,
            use_summary=use_summary,
        )
        await self.embedding_tracker.decrement(semantic_msg_id, is_leaf=True)
        return True

    async def _vectorize_directory(
        self, uri, context_type, abstract, overview, ctx=None, semantic_msg_id=None
    ):
        self.vectorize_telemetry_ids.append(get_current_telemetry().telemetry_id)
        await super()._vectorize_directory(
            uri,
            context_type,
            abstract,
            overview,
            ctx=ctx,
            semantic_msg_id=semantic_msg_id,
        )
        await self.embedding_tracker.decrement(semantic_msg_id)
        await self.embedding_tracker.decrement(semantic_msg_id)

    async def _patch_file_summary(
        self,
        file_path,
        summary,
        ctx=None,
        semantic_msg_id=None,
    ):
        await self.embedding_tracker.decrement(semantic_msg_id, is_leaf=False)
        return True


@pytest.mark.asyncio
async def test_semantic_dag_does_not_seal_leaf_when_directory_discovery_fails(monkeypatch):
    root_uri = "viking://resources/root"
    events = []
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        lambda: _FailingListVikingFS({}),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _StreamingTracker(events),
    )

    executor = SemanticDagExecutor(
        processor=_FakeProcessor(),
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id="semantic-1",
        telemetry_id="telemetry-1",
    )

    with pytest.raises(RuntimeError, match="directory listing unavailable"):
        await executor.run(root_uri)

    assert "seal_leaf" not in events
    assert "seal" not in events
    assert events[-1] == "discard"


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

    # Mock lock layer: LockContext as no-op passthrough
    mock_handle = MagicMock()
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aenter__",
        AsyncMock(return_value=mock_handle),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aexit__",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: MagicMock(),
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
        [f"{root_uri}/a.txt", f"{root_uri}/b.txt", f"{root_uri}/child/c.txt"]
    )


@pytest.mark.asyncio
async def test_semantic_dag_streams_file_vectorization_before_parent_overviews(monkeypatch):
    root_uri = "viking://resources/root"
    child_uri = f"{root_uri}/child"
    file_uri = f"{child_uri}/leaf.txt"
    tree = {
        root_uri: [{"name": "child", "isDir": True}],
        child_uri: [{"name": "leaf.txt", "isDir": False}],
    }
    events = []
    tracker = _StreamingTracker(events)
    fake_fs = _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: tracker,
    )

    processor = _StreamingProcessor(events)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=ctx,
        semantic_msg_id="semantic-1",
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=True))

    await executor.run(root_uri)

    assert events[0] == "register_open"
    assert events.index(f"vectorize:{file_uri}") < events.index(f"overview:{child_uri}")
    assert events.index(f"vectorize:{file_uri}") < events.index(f"overview:{root_uri}")
    assert "add:1:1" in events
    assert events.count("seal_leaf") == 1
    assert events[-1] == "seal"


@pytest.mark.asyncio
async def test_semantic_dag_persists_content_only_text_embedding_before_summary(monkeypatch):
    root_uri = "viking://resources/root"
    fake_fs = _FakeVikingFS(
        {
            root_uri: [{"name": "leaf.md", "isDir": False}],
        }
    )
    events = []
    tracker = _StreamingTracker(events)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: tracker,
    )

    processor = _EmbeddingFirstProcessor()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id="semantic-1",
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=True))

    run_task = asyncio.create_task(executor.run(root_uri))
    await processor.summary_started.wait()

    try:
        assert processor.events == ["vectorize_persisted", "summary_started"]
        assert processor.vectorize_summaries == [{"name": "leaf.md", "summary": ""}]
    finally:
        processor.allow_summary.set()
        await run_task

    assert processor.metadata_patches == []
    assert "add:1:0" in events


@pytest.mark.asyncio
async def test_semantic_dag_marks_content_only_leaf_indexed_while_summary_is_blocked(
    monkeypatch,
):
    root_uri = "viking://resources/root"
    telemetry_id = "telemetry-embedding-first"
    semantic_msg_id = "semantic-embedding-first"
    fake_fs = _FakeVikingFS(
        {
            root_uri: [{"name": "leaf.md", "isDir": False}],
        }
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(EmbeddingTaskTracker, "_instance", None)
    monkeypatch.setattr(EmbeddingTaskTracker, "_initialized", False)
    embedding_tracker = EmbeddingTaskTracker.get_instance()
    request_tracker = RequestWaitTracker()
    request_tracker.register_request(telemetry_id)
    request_tracker.register_semantic_root(telemetry_id, semantic_msg_id)

    processor = _EmbeddingFirstCompletingProcessor(embedding_tracker)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id=semantic_msg_id,
        telemetry_id=telemetry_id,
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=True))

    run_task = asyncio.create_task(executor.run(root_uri))
    await processor.summary_started.wait()

    try:
        assert request_tracker.is_leaf_indexed(telemetry_id) is True
    finally:
        processor.allow_summary.set()
        await run_task
        request_tracker.cleanup(telemetry_id)


@pytest.mark.asyncio
async def test_semantic_dag_keeps_summary_dependent_leaf_summary_first(monkeypatch):
    root_uri = "viking://resources/root"
    fake_fs = _FakeVikingFS(
        {
            root_uri: [{"name": "leaf.md", "isDir": False}],
        }
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.resolve_leaf_vectorization_plan",
        lambda *_args, **_kwargs: SimpleNamespace(requires_summary=True),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _StreamingTracker([]),
    )

    processor = _EmbeddingFirstProcessor()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id="semantic-1",
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=True))

    run_task = asyncio.create_task(executor.run(root_uri))
    await processor.summary_started.wait()

    try:
        assert processor.events == ["summary_started"]
    finally:
        processor.allow_summary.set()
        await run_task

    assert processor.events == ["summary_started", "vectorize_persisted"]
    assert processor.vectorize_summaries == [
        {"name": "leaf.md", "summary": "generated summary"},
    ]
    assert processor.metadata_patches == []


@pytest.mark.asyncio
async def test_semantic_dag_falls_back_to_summary_when_direct_leaf_read_fails(monkeypatch):
    root_uri = "viking://resources/root"
    fake_fs = _FakeVikingFS(
        {
            root_uri: [{"name": "leaf.md", "isDir": False}],
        }
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _StreamingTracker([]),
    )

    processor = _EmbeddingFirstFallbackProcessor()
    processor.allow_summary.set()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id="semantic-1",
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=True))

    await executor.run(root_uri)

    assert processor.vectorize_summaries == [
        {"name": "leaf.md", "summary": ""},
        {"name": "leaf.md", "summary": "generated summary"},
    ]
    assert processor.metadata_patches == []


@pytest.mark.asyncio
async def test_semantic_dag_compensates_full_tracker_when_metadata_patch_is_not_enqueued(
    monkeypatch,
):
    root_uri = "viking://resources/root"
    telemetry_id = "telemetry-patch-rejected"
    semantic_msg_id = "semantic-patch-rejected"
    fake_fs = _FakeVikingFS(
        {
            root_uri: [{"name": "leaf.md", "isDir": False}],
        }
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(EmbeddingTaskTracker, "_instance", None)
    monkeypatch.setattr(EmbeddingTaskTracker, "_initialized", False)
    embedding_tracker = EmbeddingTaskTracker.get_instance()
    request_tracker = RequestWaitTracker()
    request_tracker.register_request(telemetry_id)
    request_tracker.register_semantic_root(telemetry_id, semantic_msg_id)

    class _BlockingCloseLock:
        def __init__(self):
            self.close_started = asyncio.Event()
            self.allow_close = asyncio.Event()

        async def close(self):
            self.close_started.set()
            await self.allow_close.wait()

    lock = _BlockingCloseLock()
    processor = _MetadataPatchRejectingProcessor()
    processor.allow_summary.set()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id=semantic_msg_id,
        telemetry_id=telemetry_id,
        lock=lock,
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=False))

    try:
        await executor.run(root_uri)
        leaf_done = asyncio.create_task(embedding_tracker.decrement(semantic_msg_id, is_leaf=True))
        await lock.close_started.wait()

        status = request_tracker.build_queue_status(telemetry_id)
        assert status["Embedding"]["error_count"] == 1
        assert request_tracker.is_complete(telemetry_id) is True
    finally:
        lock.allow_close.set()
        if "leaf_done" in locals():
            await leaf_done
        await embedding_tracker.discard(semantic_msg_id)
        request_tracker.cleanup(telemetry_id)


@pytest.mark.asyncio
async def test_semantic_dag_enqueues_metadata_patch_only_after_leaf_embedding_finishes(
    monkeypatch,
):
    root_uri = "viking://resources/root"
    fake_fs = _FakeVikingFS(
        {
            root_uri: [{"name": "leaf.md", "isDir": False}],
        }
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(EmbeddingTaskTracker, "_instance", None)
    monkeypatch.setattr(EmbeddingTaskTracker, "_initialized", False)
    embedding_tracker = EmbeddingTaskTracker.get_instance()

    processor = _EmbeddingFirstProcessor()
    processor.allow_summary.set()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id="semantic-1",
        telemetry_id="telemetry-1",
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=False))

    await executor.run(root_uri)

    assert processor.metadata_patches == []
    await embedding_tracker.decrement("semantic-1", is_leaf=True)
    assert processor.metadata_patches == [
        ("viking://resources/root/leaf.md", "generated summary", "semantic-1")
    ]
    await embedding_tracker.discard("semantic-1")


@pytest.mark.asyncio
async def test_semantic_dag_leaf_milestone_does_not_wait_for_metadata_patch_enqueue(
    monkeypatch,
):
    root_uri = "viking://resources/root"
    telemetry_id = "telemetry-patch-blocked"
    semantic_msg_id = "semantic-patch-blocked"
    fake_fs = _FakeVikingFS(
        {
            root_uri: [{"name": "leaf.md", "isDir": False}],
        }
    )

    class _BlockingPatchProcessor(_EmbeddingFirstProcessor):
        def __init__(self):
            super().__init__()
            self.patch_started = asyncio.Event()
            self.allow_patch = asyncio.Event()

        async def _patch_file_summary(
            self,
            file_path,
            summary,
            ctx=None,
            semantic_msg_id=None,
        ):
            del file_path, summary, ctx, semantic_msg_id
            self.patch_started.set()
            await self.allow_patch.wait()
            return True

    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(EmbeddingTaskTracker, "_instance", None)
    monkeypatch.setattr(EmbeddingTaskTracker, "_initialized", False)
    embedding_tracker = EmbeddingTaskTracker.get_instance()
    request_tracker = RequestWaitTracker()
    request_tracker.register_request(telemetry_id)
    request_tracker.register_semantic_root(telemetry_id, semantic_msg_id)
    processor = _BlockingPatchProcessor()
    processor.allow_summary.set()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id=semantic_msg_id,
        telemetry_id=telemetry_id,
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=False))

    try:
        await executor.run(root_uri)
        leaf_done = asyncio.create_task(embedding_tracker.decrement(semantic_msg_id, is_leaf=True))
        await processor.patch_started.wait()

        assert request_tracker.is_leaf_indexed(telemetry_id) is True
    finally:
        processor.allow_patch.set()
        if "leaf_done" in locals():
            await leaf_done
        await embedding_tracker.discard(semantic_msg_id)
        request_tracker.cleanup(telemetry_id)


@pytest.mark.asyncio
async def test_semantic_dag_drops_pending_metadata_patch_after_leaf_embedding_failure(
    monkeypatch,
):
    root_uri = "viking://resources/root"
    telemetry_id = "telemetry-leaf-failure"
    semantic_msg_id = "semantic-leaf-failure"
    fake_fs = _FakeVikingFS(
        {
            root_uri: [{"name": "leaf.md", "isDir": False}],
        }
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(EmbeddingTaskTracker, "_instance", None)
    monkeypatch.setattr(EmbeddingTaskTracker, "_initialized", False)
    embedding_tracker = EmbeddingTaskTracker.get_instance()
    request_tracker = RequestWaitTracker()
    request_tracker.register_request(telemetry_id)
    request_tracker.register_semantic_root(telemetry_id, semantic_msg_id)

    processor = _EmbeddingFirstProcessor()
    processor.allow_summary.set()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id=semantic_msg_id,
        telemetry_id=telemetry_id,
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=False))

    try:
        await executor.run(root_uri)
        request_tracker.record_embedding_error(
            telemetry_id,
            "leaf embedding failed",
            is_leaf=True,
        )
        await embedding_tracker.decrement(semantic_msg_id, is_leaf=True)

        assert processor.metadata_patches == []
        assert request_tracker.is_leaf_indexed(telemetry_id) is False
        assert request_tracker.is_complete(telemetry_id) is True
    finally:
        await embedding_tracker.discard(semantic_msg_id)
        request_tracker.cleanup(telemetry_id)


@pytest.mark.asyncio
async def test_semantic_dag_metadata_patch_uses_own_request_telemetry(monkeypatch):
    from openviking.telemetry import bind_telemetry
    from openviking.telemetry.operation import OperationTelemetry

    captured = []

    class _TelemetryProcessor(_FakeProcessor):
        async def _patch_file_summary(
            self,
            file_path,
            summary,
            ctx=None,
            semantic_msg_id=None,
        ):
            del file_path, summary, ctx, semantic_msg_id
            captured.append(get_current_telemetry().telemetry_id)
            return True

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        lambda: _FakeVikingFS({}),
    )
    executor = SemanticDagExecutor(
        processor=_TelemetryProcessor(),
        context_type="resource",
        max_concurrent_llm=1,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id="semantic-b",
        telemetry_id="telemetry-b",
    )
    executor._embedding_tracker = _CompensatingStreamingTracker([])
    wrong_context = OperationTelemetry(operation="request-a", enabled=False)
    wrong_context.telemetry_id = "telemetry-a"

    with bind_telemetry(wrong_context):
        await executor._enqueue_registered_metadata_patch(
            "viking://resources/root/leaf.md",
            "generated summary",
        )

    assert captured == ["telemetry-b"]


@pytest.mark.asyncio
async def test_semantic_dag_mixed_leaves_waits_for_summary_dependent_embedding(monkeypatch):
    root_uri = "viking://resources/root"
    telemetry_id = "telemetry-mixed"
    semantic_msg_id = "semantic-mixed"
    fake_fs = _FakeVikingFS(
        {
            root_uri: [
                {"name": "early.md", "isDir": False},
                {"name": "summary-dependent.png", "isDir": False},
            ],
        }
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(EmbeddingTaskTracker, "_instance", None)
    monkeypatch.setattr(EmbeddingTaskTracker, "_initialized", False)
    embedding_tracker = EmbeddingTaskTracker.get_instance()
    request_tracker = RequestWaitTracker()
    request_tracker.register_request(telemetry_id)
    request_tracker.register_semantic_root(telemetry_id, semantic_msg_id)

    processor = _MixedLeafProcessor(embedding_tracker)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        semantic_msg_id=semantic_msg_id,
        telemetry_id=telemetry_id,
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=True))

    run_task = asyncio.create_task(executor.run(root_uri))
    await processor.media_summary_started.wait()

    try:
        assert request_tracker.is_leaf_indexed(telemetry_id) is False
    finally:
        processor.allow_media_summary.set()
        await run_task

    assert request_tracker.is_leaf_indexed(telemetry_id) is True
    request_tracker.cleanup(telemetry_id)


@pytest.mark.asyncio
async def test_semantic_dag_marks_leaf_indexed_after_leaf_embedding_before_overviews(monkeypatch):
    root_uri = "viking://resources/root"
    child_uri = f"{root_uri}/child"
    telemetry_id = "telemetry-streaming"
    semantic_msg_id = "semantic-streaming"
    tree = {
        root_uri: [{"name": "child", "isDir": True}],
        child_uri: [{"name": "leaf.txt", "isDir": False}],
    }
    fake_fs = _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(EmbeddingTaskTracker, "_instance", None)
    monkeypatch.setattr(EmbeddingTaskTracker, "_initialized", False)
    embedding_tracker = EmbeddingTaskTracker.get_instance()
    request_tracker = RequestWaitTracker()
    request_tracker.register_request(telemetry_id)
    request_tracker.register_semantic_root(telemetry_id, semantic_msg_id)

    processor = _CompletingEmbeddingProcessor(
        embedding_tracker,
        request_tracker,
        telemetry_id,
    )
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=ctx,
        semantic_msg_id=semantic_msg_id,
        telemetry_id=telemetry_id,
    )
    monkeypatch.setattr(executor, "_write_directory_semantics", AsyncMock(return_value=True))

    try:
        await executor.run(root_uri)

        assert processor.leaf_indexed_at_overview[child_uri] is True
        assert processor.leaf_indexed_at_overview[root_uri] is True
        assert processor.vectorize_telemetry_ids == [telemetry_id, telemetry_id, telemetry_id]
        assert request_tracker.is_leaf_indexed(telemetry_id) is True
        assert request_tracker.is_complete(telemetry_id) is True
    finally:
        request_tracker.cleanup(telemetry_id)


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

    mock_handle = MagicMock()
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aenter__",
        AsyncMock(return_value=mock_handle),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aexit__",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: MagicMock(),
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
