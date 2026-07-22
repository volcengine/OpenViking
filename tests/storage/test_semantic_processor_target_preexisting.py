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

    async def ls(self, uri, show_all_hidden=False, node_limit=None, ctx=None):
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
            lock=NO_LOCK,
        )

    fake_fs.ls.assert_not_awaited()
    fake_fs.rm.assert_not_awaited()


class _PreserveTestFS:
    """Fake FS with source (temp) and target (resources/root) directories.

    Source has: a.md (changed), b.md (same), new_file.md (new)
    Target has: a.md (old), b.md (same), user_notes.md (user-managed),
                user_dir/ (user-managed directory)
    """

    def __init__(self):
        self.contents = {
            # Source (temp)
            "viking://temp/import/a.md": "new content",
            "viking://temp/import/b.md": "same",
            "viking://temp/import/new_file.md": "brand new",
            # Target
            "viking://resources/root/a.md": "old content",
            "viking://resources/root/b.md": "same",
            "viking://resources/root/user_notes.md": "user-managed notes",
            "viking://resources/root/user_dir/inner.md": "user-managed inner",
        }
        self.entries = {
            "viking://temp/import": [
                {"name": "a.md", "isDir": False},
                {"name": "b.md", "isDir": False},
                {"name": "new_file.md", "isDir": False},
            ],
            "viking://resources/root": [
                {"name": "a.md", "isDir": False},
                {"name": "b.md", "isDir": False},
                {"name": "user_notes.md", "isDir": False},
                {"name": "user_dir", "isDir": True},
            ],
            "viking://resources/root/user_dir": [
                {"name": "inner.md", "isDir": False},
            ],
        }
        self.deleted = []
        self.deleted_temp = []

    async def exists(self, uri, ctx=None):
        return uri in self.entries or uri in self.contents

    async def ls(self, uri, show_all_hidden=False, node_limit=None, ctx=None):
        return self.entries.get(uri, [])

    async def stat(self, uri, ctx=None):
        return {"size": len(self.contents.get(uri, ""))}

    async def read_file(self, uri, ctx=None):
        return self.contents.get(uri, "")

    async def rm(self, uri, recursive=False, ctx=None, lock_handle=None):
        self.deleted.append(uri)
        self.contents.pop(uri, None)
        self.entries.pop(uri, None)

    async def mv(self, src, dst, ctx=None, lock_handle=None):
        self.contents[dst] = self.contents.pop(src, "")

    async def mkdir(self, uri, exist_ok=False, ctx=None):
        self.entries.setdefault(uri, [])

    async def delete_temp(self, uri, ctx=None):
        self.deleted_temp.append(uri)


@pytest.mark.asyncio
async def test_preserve_target_only_protects_user_files(monkeypatch):
    """When preserve_target_only=True, user-managed files are not deleted."""
    fake_fs = _PreserveTestFS()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )

    diff = await SemanticProcessor()._sync_topdown_recursive(
        "viking://temp/import",
        "viking://resources/root",
        lock=NO_LOCK,
        preserve_target_only=True,
    )

    # a.md should be updated (exists in both, content changed)
    assert fake_fs.contents["viking://resources/root/a.md"] == "new content"
    assert "viking://resources/root/a.md" in diff.updated_files

    # new_file.md should be added (exists only in source)
    assert "viking://resources/root/new_file.md" in diff.added_files

    # user_notes.md should NOT be deleted (user-managed)
    assert "viking://resources/root/user_notes.md" not in fake_fs.deleted
    assert "viking://resources/root/user_notes.md" in fake_fs.contents

    # user_dir/ should NOT be deleted (user-managed)
    assert "viking://resources/root/user_dir" not in fake_fs.deleted
    assert "viking://resources/root/user_dir" in fake_fs.entries

    # Nothing should be in deleted_files or deleted_dirs
    assert len(diff.deleted_files) == 0
    assert len(diff.deleted_dirs) == 0


@pytest.mark.asyncio
async def test_default_sync_deletes_target_only_files(monkeypatch):
    """When preserve_target_only=False (default), target-only files ARE deleted.

    This confirms the flag actually controls deletion behavior.
    """
    fake_fs = _PreserveTestFS()
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )

    diff = await SemanticProcessor()._sync_topdown_recursive(
        "viking://temp/import",
        "viking://resources/root",
        lock=NO_LOCK,
    )

    # user_notes.md should be deleted (default behavior)
    assert "viking://resources/root/user_notes.md" in fake_fs.deleted
    assert "viking://resources/root/user_notes.md" not in fake_fs.contents

    # user_dir/ should be deleted
    assert "viking://resources/root/user_dir" in fake_fs.deleted
    assert "viking://resources/root/user_dir" not in fake_fs.entries
