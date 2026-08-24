from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.viking_fs import SyncDiff


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


class _FakeDagExecutor:
    calls = []
    runs = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stale = False
        _FakeDagExecutor.calls.append(kwargs)

    async def run(self, root_uri):
        self.root_uri = root_uri
        _FakeDagExecutor.runs.append(root_uri)

    def get_stats(self):
        from openviking.storage.queuefs.semantic_dag import DagStats

        return DagStats()


@pytest.mark.asyncio
async def test_target_source_syncs_before_semantic_dag(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )

    _FakeDagExecutor.calls = []
    _FakeDagExecutor.runs = []
    processor = SemanticProcessor()
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
        target_preexisting=True,
    )

    await processor.on_dequeue(msg.to_dict())

    assert _FakeDagExecutor.calls[0]["incremental_update"] is True
    assert _FakeDagExecutor.calls[0]["target_uri"] == "viking://resources/org/repo"
    assert _FakeDagExecutor.calls[0]["changes"] == {
        "added": [],
        "modified": ["viking://resources/org/repo/a.md"],
        "deleted": [],
    }
    assert _FakeDagExecutor.runs == ["viking://resources/org/repo"]


@pytest.mark.asyncio
async def test_stale_content_write_keeps_file_work_without_directory_aggregation(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.is_semantic_msg_stale",
        lambda msg: bool(msg.coalesce_key),
    )

    _FakeDagExecutor.calls = []
    _FakeDagExecutor.runs = []
    processor = SemanticProcessor()
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

    assert _FakeDagExecutor.calls[0]["aggregate_directory"] is False
    assert _FakeDagExecutor.calls[0]["changes"] == {"modified": [changed]}
    assert _FakeDagExecutor.calls[0]["coalesce_key"] == ""
    assert _FakeDagExecutor.runs == ["viking://resources/wiki"]
    processor._enqueue_parent_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("recursive", [False, True])
async def test_memory_reindex_uses_semantic_dag(monkeypatch, recursive):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: _FakeVikingFS(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeDagExecutor,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock=None, close=AsyncMock())),
    )

    _FakeDagExecutor.calls = []
    _FakeDagExecutor.runs = []
    processor = SemanticProcessor()
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
    assert _FakeDagExecutor.calls[0]["context_type"] == "memory"
    assert _FakeDagExecutor.calls[0]["recursive"] is recursive
    assert _FakeDagExecutor.runs == [msg.uri]


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

    processor = SemanticProcessor()
    processor._process_memory_directory = AsyncMock()
    msg = SemanticMsg(
        uri="viking://user/alice/memories/preferences",
        context_type="memory",
        generation_trigger="reindex",
    )

    await processor.on_dequeue(msg.to_dict())

    processor._process_memory_directory.assert_awaited_once()


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

    diff = await SemanticProcessor()._sync_topdown_recursive(
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

    await SemanticProcessor()._sync_topdown_recursive(
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
        await SemanticProcessor()._sync_topdown_recursive(
            "viking://temp/missing",
            "viking://resources/root",
            lock=None,
        )

    fake_fs.sync_tree.assert_not_awaited()
    fake_fs.rm.assert_not_awaited()
