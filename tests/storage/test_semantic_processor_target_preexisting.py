import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.service.task_work_index import (
    bind_task_context,
    get_task_context,
    prepare_task_payload,
    record_enqueued_work,
)
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.viking_fs import SyncDiff
from tests.test_task_tracker import _FakeAgfs


def _processor(**kwargs):
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace()))
    return SemanticProcessor(vlm_resolver=resolver, **kwargs)


class _FakeVikingFS:
    async def exists(self, uri, ctx=None):
        return True


class _SyncWrapperVikingFS:
    """Fake FS that verifies the wrapper calls sync_tree and post-processes correctly."""

    def __init__(self, target_exists=True):
        self.target_exists = target_exists
        self.sync_tree_calls = []
        self.deleted_temp = []
        self.mutation_leases = []
        self.sidecar_rewrite_calls = []

    async def exists(self, uri, ctx=None):
        if uri.startswith("viking://resources/root"):
            return self.target_exists
        return True

    async def sync_tree(
        self,
        root_uri,
        target_uri,
        *,
        ctx=None,
        file_change_status=None,
        lease_ref=None,
        delete_temp_after=False,
    ):
        self.sync_tree_calls.append(
            (root_uri, target_uri, file_change_status, lease_ref, delete_temp_after)
        )
        self.mutation_leases.append(("sync_tree", root_uri, target_uri, lease_ref))
        return SyncDiff(updated_files=["viking://resources/root/a.md"])

    async def delete_temp(self, uri, ctx=None):
        self.mutation_leases.append(("delete_temp", uri, None))
        self.deleted_temp.append(uri)

    async def read_file(self, uri, ctx=None):
        return "{}"

    async def stat(self, uri, ctx=None):
        raise FileNotFoundError(uri)

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.mutation_leases.append(("write_file", uri, lease_ref))

    async def glob(self, pattern, uri=None, ctx=None):
        return {"matches": []}


class _FakeTreeExecutor:
    calls = []
    runs = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stale = False
        _FakeTreeExecutor.calls.append(kwargs)

    async def run(self, root_uri):
        self.root_uri = root_uri
        _FakeTreeExecutor.runs.append(root_uri)

    def get_stats(self):
        from openviking.storage.queuefs.semantic_executor import SemanticTreeStats

        return SemanticTreeStats()


@pytest.mark.asyncio
async def test_vectorize_directory_returns_enqueued_levels(monkeypatch):
    vectorize = AsyncMock(return_value={0, 1})
    monkeypatch.setattr("openviking.utils.embedding_utils.vectorize_directory_meta", vectorize)
    processor = object.__new__(SemanticProcessor)
    processor._default_ctx = object()

    result = await processor._vectorize_directory(
        "viking://resources/root",
        "resource",
        "abstract",
        "overview",
    )

    assert result == {0, 1}


@pytest.mark.asyncio
async def test_target_source_syncs_before_semantic_executor(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticTreeExecutor",
        _FakeTreeExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )

    _FakeTreeExecutor.calls = []
    _FakeTreeExecutor.runs = []
    processor = _processor()
    processor._enqueue_parent_refresh = AsyncMock()
    processor._sync_topdown_recursive = AsyncMock(
        return_value=SyncDiff(
            updated_files=["viking://resources/org/repo/a.md"],
        )
    )
    msg = SemanticMsg(
        uri="viking://temp/import_root/repository",
        target_uri="viking://resources/org/repo",
        context_type="resource",
    )

    await processor.on_dequeue(msg.to_dict())

    assert _FakeTreeExecutor.calls[0]["incremental_update"] is True
    assert _FakeTreeExecutor.calls[0]["target_uri"] == "viking://resources/org/repo"
    assert _FakeTreeExecutor.calls[0]["changes"] == {
        "added": [],
        "modified": ["viking://resources/org/repo/a.md"],
        "deleted": [],
    }
    assert _FakeTreeExecutor.runs == ["viking://resources/org/repo"]


@pytest.mark.asyncio
@pytest.mark.parametrize("file_count", [1, 2])
async def test_content_write_waits_for_file_embedding_but_not_directory(monkeypatch, file_count):
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    task = await tracker.create("content_write", account_id="acc", user_id="user")
    await tracker.start(task.task_id, account_id="acc", user_id="user")
    embedding_wait = asyncio.Event()
    directory_started = asyncio.Event()
    finish_directory = asyncio.Event()
    released = asyncio.Event()
    directory_runs = []
    queued = []
    changed = [f"viking://resources/docs/{index}.md" for index in range(file_count)]
    original_wait = tracker.wait_for_work

    async def wait_for_work(*args, **kwargs):
        embedding_wait.set()
        await original_wait(*args, **kwargs)

    monkeypatch.setattr(tracker, "wait_for_work", wait_for_work)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_work.get_task_tracker", lambda: tracker
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: _FakeVikingFS()
    )

    async def close():
        released.set()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=close)),
    )

    class Executor(_FakeTreeExecutor):
        file_abstracts = dict.fromkeys(changed, "file summary")

        async def run(self, root_uri):
            if self.kwargs["generation_trigger"] == "content_write":
                assert self.kwargs["aggregate_directory"] is False
                assert get_task_context().task_id == task.task_id
                for index in range(file_count):
                    work_id = f"embedding-{index}"
                    await tracker.register_work(
                        task.task_id, work_id, "Embedding", account_id="acc", user_id="user"
                    )
                    record_enqueued_work("Embedding", work_id)
            else:
                assert get_task_context() is None
                assert self.kwargs["aggregate_directory"] is True
                assert self.kwargs["telemetry_id"] == ""
                directory_started.set()
                await finish_directory.wait()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticTreeExecutor", Executor
    )
    processor = _processor()
    processor._enqueue_parent_refresh = AsyncMock()
    processor._embedding_worker_stopped = lambda: False

    async def enqueue(msg):
        payload, metadata = prepare_task_payload(msg.to_dict())
        assert metadata is None
        queued.append(msg)
        directory_runs.append(asyncio.create_task(processor.on_dequeue(payload)))

    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(
            SEMANTIC="Semantic",
            get_queue=lambda *a, **kw: SimpleNamespace(enqueue=enqueue),
        ),
    )
    msg = SemanticMsg(
        uri="viking://resources/docs",
        context_type="resource",
        account_id="acc",
        user_id="user",
        recursive=False,
        changes={"modified": changed},
        lock_handoff={"lease_ref": "file-lock"},
        telemetry_id="write-telemetry",
        generation_trigger="content_write",
    )
    with bind_task_context(task.task_id, "acc", "user", enqueued_work=[]):
        file_run = asyncio.create_task(processor.on_dequeue(msg.to_dict()))
    try:
        await asyncio.wait_for(embedding_wait.wait(), timeout=2)
        assert not file_run.done()
        assert not released.is_set()
        assert queued == []
        for index in range(file_count):
            await tracker.mark_work_done(
                task.task_id, f"embedding-{index}", account_id="acc", user_id="user"
            )
        result = await asyncio.wait_for(file_run, timeout=2)
        await asyncio.wait_for(directory_started.wait(), timeout=2)
        assert result.error is None
        assert released.is_set()
        assert not directory_runs[0].done()
        assert queued[0].uri == msg.uri
        assert queued[0].lock_handoff is None
        assert queued[0].task_id == ""
        assert queued[0].file_abstracts == Executor.file_abstracts
        processor._enqueue_parent_refresh.assert_not_awaited()
    finally:
        finish_directory.set()
        if not file_run.done():
            file_run.cancel()
        await asyncio.gather(file_run, *directory_runs, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_content_write_keeps_file_work_without_directory_aggregation(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticTreeExecutor",
        _FakeTreeExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.is_semantic_msg_stale",
        lambda msg: bool(msg.coalesce_key),
    )

    _FakeTreeExecutor.calls = []
    _FakeTreeExecutor.runs = []
    processor = _processor()
    processor._enqueue_parent_refresh = AsyncMock()
    changed = "viking://resources/wiki/changed.md"
    msg = SemanticMsg(
        uri="viking://resources/wiki",
        context_type="resource",
        recursive=False,
        coalesce_key="resource|wiki",
        coalesce_version=1,
        changes={"modified": [changed], "deleted": ["viking://resources/wiki/old.md"]},
        generation_trigger="content_write",
    )

    await processor.on_dequeue(msg.to_dict())

    assert _FakeTreeExecutor.calls[0]["aggregate_directory"] is False
    assert _FakeTreeExecutor.calls[0]["changes"] == {"modified": [changed]}
    assert _FakeTreeExecutor.calls[0]["coalesce_key"] == ""
    assert _FakeTreeExecutor.runs == ["viking://resources/wiki"]
    processor._enqueue_parent_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("recursive", [False, True])
async def test_memory_reindex_uses_semantic_executor(monkeypatch, recursive):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticTreeExecutor",
        _FakeTreeExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )

    _FakeTreeExecutor.calls = []
    _FakeTreeExecutor.runs = []
    processor = _processor()
    processor._process_memory_directory = AsyncMock()
    msg = SemanticMsg(
        uri="viking://user/alice/memories/preferences",
        context_type="memory",
        recursive=recursive,
        generation_trigger="reindex",
        use_hierarchical_aggregation=True,
    )

    await processor.on_dequeue(msg.to_dict())

    processor._process_memory_directory.assert_not_awaited()
    assert _FakeTreeExecutor.calls[0]["context_type"] == "memory"
    assert _FakeTreeExecutor.calls[0]["recursive"] is recursive
    assert _FakeTreeExecutor.runs == [msg.uri]


@pytest.mark.asyncio
async def test_memory_trigger_does_not_select_hierarchical_aggregation(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )

    processor = _processor()
    processor._process_memory_directory = AsyncMock()
    msg = SemanticMsg(
        uri="viking://user/alice/memories/preferences",
        context_type="memory",
        generation_trigger="reindex",
    )

    await processor.on_dequeue(msg.to_dict())

    processor._process_memory_directory.assert_awaited_once()


@pytest.mark.asyncio
async def test_content_copy_does_not_enqueue_ancestor_refresh(monkeypatch):
    processor = _processor()
    plan_refresh = AsyncMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.plan_abstract_overview_refresh",
        plan_refresh,
    )
    msg = SemanticMsg(
        uri="viking://resources/archive",
        context_type="resource",
        generation_trigger="content_copy",
    )

    await processor._enqueue_parent_refresh(
        msg,
        "viking://resources/archive/copied.jpg",
        l0_body_changed=True,
    )

    plan_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_wrapper_delegates_to_sync_tree_and_cleans_temp(monkeypatch):
    """The wrapper calls viking_fs.sync_tree and then deletes the temp tree."""
    fake_fs = _SyncWrapperVikingFS(target_exists=True)
    lease = {
        "lease_ref": "outer-tree-ref",
        "owner_id": "outer-tree-owner",
        "owned": False,
    }
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.rewrite_image_uris",
        AsyncMock(),
    )

    diff = await _processor()._sync_topdown_recursive(
        "viking://temp/import",
        "viking://resources/root",
        lock=lease,
    )

    assert len(fake_fs.sync_tree_calls) == 1
    root_uri, target_uri, fcs, lr, dta = fake_fs.sync_tree_calls[0]
    assert root_uri == "viking://temp/import"
    assert target_uri == "viking://resources/root"
    assert lr is lease
    assert dta is False
    assert diff.to_changes() == {
        "added": [],
        "modified": ["viking://resources/root/a.md"],
        "deleted": [],
    }
    assert fake_fs.deleted_temp == ["viking://temp/import"]


@pytest.mark.asyncio
async def test_sync_wrapper_whole_tree_mv_for_new_target(monkeypatch):
    """When the target does not exist, sync_tree does a full mv and the wrapper does not delete (source is gone)."""
    fake_fs = _SyncWrapperVikingFS(target_exists=False)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.rewrite_image_uris",
        AsyncMock(),
    )

    await _processor()._sync_topdown_recursive(
        "viking://temp/import",
        "viking://resources/root",
        lock=None,
    )

    assert len(fake_fs.sync_tree_calls) == 1
    assert fake_fs.deleted_temp == []  # whole-tree mv already consumed the source


@pytest.mark.asyncio
async def test_sync_missing_source_never_touches_target(monkeypatch):
    fake_fs = AsyncMock()
    fake_fs.exists.return_value = False
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )

    with pytest.raises(FileNotFoundError, match="refusing to sync"):
        await _processor()._sync_topdown_recursive(
            "viking://temp/missing",
            "viking://resources/root",
            lock=None,
        )

    fake_fs.sync_tree.assert_not_awaited()
    fake_fs.rm.assert_not_awaited()
