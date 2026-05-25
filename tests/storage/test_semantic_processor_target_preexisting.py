from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import DiffResult, SemanticProcessor
from openviking.storage.transaction import NO_LOCK


class _FakeVikingFS:
    async def exists(self, uri, ctx=None):
        return True


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

    async def exists(self, uri, ctx=None):
        return uri in self.entries

    async def ls(self, uri, show_all_hidden=False, ctx=None):
        return self.entries.get(uri, [])

    async def stat(self, uri, ctx=None):
        return {"size": len(self.contents.get(uri, ""))}

    async def read_file(self, uri, ctx=None):
        return self.contents.get(uri, "")

    async def rm(self, uri, recursive=False, ctx=None, lock_handle=None):
        self.contents.pop(uri, None)

    async def mv(self, src, dst, ctx=None, lock_handle=None):
        self.contents[dst] = self.contents.pop(src)

    async def mkdir(self, uri, exist_ok=False, ctx=None):
        self.entries.setdefault(uri, [])

    async def delete_temp(self, uri, ctx=None):
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
        AsyncMock(return_value=SimpleNamespace(lock=NO_LOCK, close=AsyncMock())),
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
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )

    diff = await SemanticProcessor()._sync_topdown_recursive(
        "viking://temp/import",
        "viking://resources/root",
        lock=NO_LOCK,
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
