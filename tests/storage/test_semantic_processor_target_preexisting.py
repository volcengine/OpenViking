from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import DiffResult, SemanticProcessor


class _FakeVikingFS:
    async def exists(self, uri, ctx=None):
        return True

    async def stat(self, uri, ctx=None):
        return {"isDir": True}


class _SyncVikingFS:
    def __init__(self):
        self.contents = {
            "viking://temp/import/a.md": "new",
            "viking://temp/import/b.md": "same",
            "viking://resources/root/a.md": "old",
            "viking://resources/root/b.md": "same",
            "viking://resources/root/.overview.md": "FILES:\n- b.md: old summary",
            "viking://resources/root/.abstract.md": "old abstract",
        }
        self.entries = {
            "viking://temp/import": [
                {"name": "a.md", "isDir": False},
                {"name": "b.md", "isDir": False},
            ],
            "viking://resources/root": [
                {"name": "a.md", "isDir": False},
                {"name": "b.md", "isDir": False},
                {"name": ".overview.md", "isDir": False},
                {"name": ".abstract.md", "isDir": False},
            ],
        }
        self.deleted_temp = []
        self.mutation_leases = []

    async def exists(self, uri, ctx=None):
        return uri in self.entries

    async def ls(self, uri, show_all_hidden=False, node_limit=None, ctx=None):
        return self.entries.get(uri, [])

    async def stat(self, uri, ctx=None):
        return {"size": len(self.contents.get(uri, "")), "isDir": uri in self.entries}

    async def read_file(self, uri, ctx=None):
        return self.contents.get(uri, "")

    async def rm(self, uri, recursive=False, ctx=None, lease_ref=None):
        self.mutation_leases.append(("rm", uri, lease_ref))
        self.contents.pop(uri, None)

    async def mv(self, src, dst, ctx=None, lease_ref=None):
        self.mutation_leases.append(("mv", dst, lease_ref))
        self.contents[dst] = self.contents.pop(src)

    async def mkdir(self, uri, exist_ok=False, ctx=None, lease_ref=None):
        self.mutation_leases.append(("mkdir", uri, lease_ref))
        self.entries.setdefault(uri, [])

    async def delete_temp(self, uri, ctx=None, lease_ref=None):
        self.mutation_leases.append(("delete_temp", uri, lease_ref))
        self.deleted_temp.append(uri)


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
        return_value=DiffResult(
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
async def test_sync_diff_reports_target_uris_and_preserves_sidecars(monkeypatch):
    fake_fs = _SyncVikingFS()
    lease = {
        "lease_ref": "outer-tree-ref",
        "owner_id": "outer-tree-owner",
        "owned": False,
    }
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )

    diff = await SemanticProcessor()._sync_topdown_recursive(
        "viking://temp/import",
        "viking://resources/root",
        lock=lease,
    )

    assert diff.to_changes() == {
        "added": [],
        "modified": ["viking://resources/root/a.md"],
        "deleted": [],
    }
    assert fake_fs.contents["viking://resources/root/a.md"] == "new"
    assert fake_fs.contents["viking://resources/root/.overview.md"] == (
        "FILES:\n- b.md: old summary"
    )
    assert fake_fs.contents["viking://resources/root/.abstract.md"] == "old abstract"
    assert fake_fs.deleted_temp == ["viking://temp/import"]
    assert fake_fs.mutation_leases
    assert all(
        mutation_lease is lease
        for operation, _, mutation_lease in fake_fs.mutation_leases
        if operation != "delete_temp"
    )
    assert fake_fs.mutation_leases[-1] == ("delete_temp", "viking://temp/import", None)


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

    fake_fs.ls.assert_not_awaited()
    fake_fs.rm.assert_not_awaited()


class _FileRootFS:
    def __init__(self):
        self.persist_calls = []
        self.deleted_temp = []

    async def stat(self, uri, ctx=None):
        return {"isDir": False}

    async def exists(self, uri, ctx=None):
        return False

    async def persist_temp_tree(self, temp_uri, target_uri, ctx=None, lease_ref=None):
        self.persist_calls.append((temp_uri, target_uri))

    async def delete_temp(self, uri, ctx=None):
        self.deleted_temp.append(uri)


@pytest.mark.asyncio
async def test_file_root_source_skips_dag_and_vectorizes(monkeypatch):
    fake_fs = _FileRootFS()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
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
    processor._vectorize_single_file = AsyncMock()
    msg = SemanticMsg(
        uri="viking://temp/import/foo.py/foo.md",
        target_uri="viking://resources/foo.py",
        context_type="resource",
    )

    await processor.on_dequeue(msg.to_dict())

    # File-shaped source: synced straight into the target, no directory DAG,
    # and the parse temp subtree is cleaned up after the copy.
    assert fake_fs.persist_calls == [
        ("viking://temp/import/foo.py/foo.md", "viking://resources/foo.py")
    ]
    assert fake_fs.deleted_temp == ["viking://temp/import/foo.py"]
    assert _FakeDagExecutor.calls == []
    assert _FakeDagExecutor.runs == []
    # The file's summary + vector come solely from the parent refresh; a
    # direct write here would race the refresh on the same embedding ID.
    processor._vectorize_single_file.assert_not_awaited()
    processor._enqueue_parent_refresh.assert_awaited_once()
    refresh_args = processor._enqueue_parent_refresh.await_args
    assert refresh_args.args[1] == "viking://resources/foo.py"
    assert refresh_args.kwargs.get("propagate_telemetry") is True
