# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.abstract_overview import parse_abstract_overview
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_fs._ops import TransferRollbackError
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)


def _user_ctx(*, actor_peer_id: str | None = None) -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
        actor_peer_id=actor_peer_id,
    )


class _CopyAGFS:
    def __init__(
        self,
        *,
        source_is_dir: bool = False,
        target_exists: bool = False,
        fail_copy: bool = False,
        parent_exists: bool = True,
    ):
        self.source_is_dir = source_is_dir
        self.target_exists = target_exists
        self.fail_copy = fail_copy
        self.parent_exists = parent_exists
        self.events: list[tuple] = []
        self.operation_lease = {
            "lease_ref": "copy-operation",
            "owner_id": "copy-owner",
            "ownership_ref": "copy-ownership",
            "owned": True,
        }

    async def stat(self, path, fs_ctx=None):
        self.events.append(("stat", path, fs_ctx))
        if path.endswith("/source") or path.endswith("/source.md"):
            return {"isDir": self.source_is_dir}
        if path == "/local/acct/resources" and self.parent_exists:
            return {"isDir": True}
        if (path.endswith("/target") or path.endswith("/target.md")) and self.target_exists:
            return {"isDir": self.source_is_dir}
        raise FileNotFoundError(path)

    async def pathlock_acquire_batch(self, requests, timeout_secs=0.0, owner_lease_ref=None):
        del timeout_secs
        self.events.append(("acquire-batch", requests, owner_lease_ref))
        return self.operation_lease

    async def pathlock_release(self, lease):
        self.events.append(("release", lease))

    async def cp(self, source, target, recursive=False, fs_ctx=None):
        self.events.append(("cp", source, target, recursive, fs_ctx))
        self.target_exists = True
        if self.fail_copy:
            raise RuntimeError("injected AGFS copy failure")

    async def rm(self, path, recursive=False, fs_ctx=None):
        self.events.append(("rm", path, recursive, fs_ctx))
        if path.endswith("/target") or path.endswith("/target.md"):
            self.target_exists = False


class _DirectoryCopyAGFS(_CopyAGFS):
    def __init__(self):
        super().__init__(source_is_dir=True)
        self.directories = {
            "/local/acct/resources",
            "/local/acct/resources/source",
            "/local/acct/resources/source/empty",
        }
        self.files = {
            "/local/acct/resources/source/.hidden": b"hidden",
            "/local/acct/resources/source/data.bin": b"\x00\xff",
        }
        self.exact_leases: list[tuple[str, dict]] = []

    async def stat(self, path, fs_ctx=None):
        self.events.append(("stat", path, fs_ctx))
        if path in self.directories:
            return {"isDir": True}
        if path in self.files:
            return {"isDir": False}
        raise FileNotFoundError(path)

    async def mkdir(self, path, fs_ctx=None):
        self.events.append(("mkdir", path, fs_ctx))
        self.directories.add(path)

    async def ls(self, path, fs_ctx=None):
        self.events.append(("ls", path, fs_ctx))
        if path not in self.directories:
            raise FileNotFoundError(path)
        prefix = f"{path.rstrip('/')}/"
        entries: dict[str, bool] = {}
        for directory in self.directories:
            if not directory.startswith(prefix):
                continue
            relative = directory[len(prefix) :]
            if relative and "/" not in relative:
                entries[relative] = True
        for file_path in self.files:
            if not file_path.startswith(prefix):
                continue
            relative = file_path[len(prefix) :]
            if relative and "/" not in relative:
                entries[relative] = False
        return [{"name": name, "isDir": is_dir} for name, is_dir in sorted(entries.items())]

    async def pathlock_acquire_exact(self, path, timeout_secs=0.0, owner_lease_ref=None):
        del timeout_secs
        lease = {
            "lease_ref": f"child-{len(self.exact_leases)}",
            "owner_id": owner_lease_ref["owner_id"],
            "ownership_ref": f"child-owner-{len(self.exact_leases)}",
            "owned": True,
        }
        self.exact_leases.append((path, lease))
        self.events.append(("acquire-exact", path, owner_lease_ref))
        return lease

    async def cp(self, source, target, recursive=False, fs_ctx=None):
        self.events.append(("cp", source, target, recursive, fs_ctx))
        self.files[target] = self.files[source]

    async def cat(self, path, offset=0, size=-1, stream=False, fs_ctx=None):
        del stream, fs_ctx
        content = self.files[path]
        return content[offset:] if size < 0 else content[offset : offset + size]

    async def write(self, path, data, max_retries=3, fs_ctx=None, auto_pathlock=True):
        del max_retries, fs_ctx, auto_pathlock
        self.files[path] = data if isinstance(data, bytes) else bytes(data)
        return path

    async def rm(self, path, recursive=False, fs_ctx=None):
        self.events.append(("rm", path, recursive, fs_ctx))
        prefix = f"{path.rstrip('/')}/"
        self.directories = {
            item for item in self.directories if item != path and not item.startswith(prefix)
        }
        self.files = {
            item: content
            for item, content in self.files.items()
            if item != path and not item.startswith(prefix)
        }


class _DirectoryMoveRollbackAGFS(_DirectoryCopyAGFS):
    def __init__(self):
        super().__init__()
        self.fail_source_delete = True

    async def rm(self, path, recursive=False, fs_ctx=None):
        if path == "/local/acct/resources/source" and self.fail_source_delete:
            self.events.append(("rm", path, recursive, fs_ctx))
            self.fail_source_delete = False
            self.files.pop(f"{path}/data.bin", None)
            self.directories.discard(f"{path}/empty")
            raise RuntimeError("injected partial source delete failure")
        await super().rm(path, recursive=recursive, fs_ctx=fs_ctx)


class _MoveRollbackAGFS(_CopyAGFS):
    def __init__(self):
        super().__init__()
        self.paths = {"/local/acct/resources/source.md"}
        self.fail_source_delete = True

    async def stat(self, path, fs_ctx=None):
        self.events.append(("stat", path, fs_ctx))
        if path == "/local/acct/resources":
            return {"isDir": True}
        if path in self.paths:
            return {"isDir": False}
        raise FileNotFoundError(path)

    async def cp(self, source, target, recursive=False, fs_ctx=None):
        self.events.append(("cp", source, target, recursive, fs_ctx))
        assert source in self.paths
        self.paths.add(target)

    async def rm(self, path, recursive=False, fs_ctx=None):
        self.events.append(("rm", path, recursive, fs_ctx))
        if path.endswith("/source.md") and self.fail_source_delete:
            self.fail_source_delete = False
            self.paths.discard(path)
            raise RuntimeError("injected source delete failure")
        self.paths.discard(path)


def _viking_fs(monkeypatch, agfs: _CopyAGFS) -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs._async_agfs = agfs
    fs.vector_store = None
    fs.acl_manager = None
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, **_kwargs: f"/local/acct/{uri.removeprefix('viking://')}",
    )
    monkeypatch.setattr(
        fs,
        "_path_to_uri",
        lambda path, **_kwargs: f"viking://{path.removeprefix('/local/acct/')}",
    )
    return fs


@pytest.mark.parametrize(
    "uri",
    ["viking://", "viking://user", "viking://resources", "viking://temp"],
)
@pytest.mark.asyncio
async def test_cp_rejects_non_root_container_sources(monkeypatch, uri):
    fs = _viking_fs(monkeypatch, _CopyAGFS(source_is_dir=True))

    with pytest.raises(PermissionDeniedError, match="container root"):
        await fs._ensure_copy_source_access(uri, recursive=True, ctx=_user_ctx())


@pytest.mark.asyncio
async def test_cp_rejects_watch_control_source(monkeypatch):
    fs = _viking_fs(monkeypatch, _CopyAGFS())

    with pytest.raises(PermissionDeniedError, match="watch-task control"):
        await fs._ensure_copy_source_access(
            "viking://resources/.watch_tasks.json",
            recursive=False,
            ctx=_ctx(),
        )


@pytest.mark.asyncio
async def test_cp_rejects_actor_peer_scope_that_can_include_hidden_peers(monkeypatch):
    fs = _viking_fs(monkeypatch, _CopyAGFS(source_is_dir=True))

    with pytest.raises(PermissionDeniedError, match="hidden peer"):
        await fs._ensure_copy_source_access(
            "viking://user/alice/peers",
            recursive=True,
            ctx=_user_ctx(actor_peer_id="peer-a"),
        )


@pytest.mark.asyncio
async def test_cp_file_uses_exact_locks_agfs_copy_then_vector_copy(monkeypatch):
    agfs = _CopyAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    vector_copy = AsyncMock()
    monkeypatch.setattr(fs, "_copy_vector_store_uris", vector_copy, raising=False)

    await fs.cp(
        "viking://resources/source.md",
        "viking://resources/target.md",
        ctx=_ctx(),
    )

    acquire = next(event for event in agfs.events if event[0] == "acquire-batch")
    assert acquire[1] == [
        {"path": "/local/acct/resources/source.md", "kind": "exact"},
        {"path": "/local/acct/resources/target.md", "kind": "exact"},
    ]
    copy_event = next(event for event in agfs.events if event[0] == "cp")
    assert copy_event[1:4] == (
        "/local/acct/resources/source.md",
        "/local/acct/resources/target.md",
        False,
    )
    assert copy_event[4]["lease_ref"] == "copy-operation"
    vector_copy.assert_awaited_once_with(
        "viking://resources/source.md",
        "viking://resources/target.md",
        recursive=False,
        ctx=_ctx(),
    )
    assert agfs.events[-1] == ("release", agfs.operation_lease)


@pytest.mark.asyncio
async def test_cp_returns_diagnostic_transfer_summary(monkeypatch):
    agfs = _CopyAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(
        fs,
        "_copy_vector_store_uris",
        AsyncMock(
            return_value=SimpleNamespace(scanned=2, written=2, deleted=0, restored=0, batches=1)
        ),
        raising=False,
    )

    result = await fs.cp(
        "viking://resources/source.md",
        "viking://resources/target.md",
        ctx=_ctx(),
    )

    assert len(result["operation_id"]) == 32
    assert result["operation"] == "copy"
    assert result["phase"] == "completed"
    assert result["files_created"] == 1
    assert result["vectors"] == {
        "scanned": 2,
        "written": 2,
        "deleted": 0,
        "restored": 0,
        "batches": 1,
    }


@pytest.mark.asyncio
async def test_cp_directory_requires_recursive_before_locking(monkeypatch):
    agfs = _CopyAGFS(source_is_dir=True)
    fs = _viking_fs(monkeypatch, agfs)

    with pytest.raises(FailedPreconditionError, match="recursive"):
        await fs.cp(
            "viking://resources/source",
            "viking://resources/target",
            recursive=False,
            ctx=_ctx(),
        )

    assert not any(event[0] == "acquire-batch" for event in agfs.events)


@pytest.mark.asyncio
async def test_cp_directory_preserves_empty_hidden_and_binary_entries(monkeypatch):
    agfs = _DirectoryCopyAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    vector_copy = AsyncMock()
    monkeypatch.setattr(fs, "_copy_vector_store_uris", vector_copy, raising=False)

    await fs.cp(
        "viking://resources/source",
        "viking://resources/target",
        recursive=True,
        ctx=_ctx(),
    )

    acquire = next(event for event in agfs.events if event[0] == "acquire-batch")
    assert acquire[1] == [
        {"path": "/local/acct/resources", "kind": "tree"},
    ]
    assert "/local/acct/resources/target/empty" in agfs.directories
    assert agfs.files["/local/acct/resources/target/.hidden"] == b"hidden"
    assert agfs.files["/local/acct/resources/target/data.bin"] == b"\x00\xff"
    assert agfs.exact_leases == []
    vector_copy.assert_awaited_once_with(
        "viking://resources/source",
        "viking://resources/target",
        recursive=True,
        ctx=_ctx(),
    )


@pytest.mark.asyncio
async def test_cp_directory_rewrites_generated_sidecar_frontmatter_and_links(monkeypatch):
    agfs = _DirectoryCopyAGFS()
    source_overview = b"""---
directory: viking://resources/source/
source:
  kind: url
  uri: https://example.com/original
generated_by:
  component: SemanticProcessor
  trigger: semantic_refresh
freshness:
  total_entries: 1
  sampled_entries: 1
  unsampled_entries: 0
  pending_child_changes: 0
---

# Source

[chapter](viking://resources/source/%E7%AB%A0%E8%8A%82.md)
"""
    source_abstract = b"""---
directory: viking://resources/source/
---

See viking://resources/source/data.bin.
"""
    nested_abstract = b"""---
directory: viking://resources/source/nested/
---

Back to viking://resources/source/data.bin.
"""
    agfs.directories.add("/local/acct/resources/source/nested")
    agfs.files["/local/acct/resources/source/.overview.md"] = source_overview
    agfs.files["/local/acct/resources/source/.abstract.md"] = source_abstract
    agfs.files["/local/acct/resources/source/nested/.abstract.md"] = nested_abstract
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(fs, "_copy_vector_store_uris", AsyncMock(), raising=False)

    await fs.cp(
        "viking://resources/source",
        "viking://resources/target",
        recursive=True,
        ctx=_ctx(),
    )

    target_overview = agfs.files["/local/acct/resources/target/.overview.md"]
    overview_doc = parse_abstract_overview(target_overview)
    assert overview_doc.metadata["directory"] == "viking://resources/target/"
    assert overview_doc.metadata["source"] == {
        "kind": "url",
        "uri": "https://example.com/original",
    }
    assert "[chapter](viking://resources/target/%E7%AB%A0%E8%8A%82.md)" in overview_doc.body
    assert "viking://resources/source" not in target_overview.decode()

    target_abstract = agfs.files["/local/acct/resources/target/.abstract.md"]
    abstract_doc = parse_abstract_overview(target_abstract)
    assert abstract_doc.metadata["directory"] == "viking://resources/target/"
    assert "viking://resources/target/data.bin" in abstract_doc.body
    assert "viking://resources/source" not in target_abstract.decode()

    target_nested = agfs.files["/local/acct/resources/target/nested/.abstract.md"]
    nested_doc = parse_abstract_overview(target_nested)
    assert nested_doc.metadata["directory"] == "viking://resources/target/nested/"
    assert "viking://resources/target/data.bin" in nested_doc.body
    assert "viking://resources/source" not in target_nested.decode()


@pytest.mark.asyncio
async def test_cp_directory_locks_both_distinct_parent_trees(monkeypatch):
    agfs = _DirectoryCopyAGFS()
    agfs.directories = {
        "/local/acct/resources",
        "/local/acct/resources/source-parent",
        "/local/acct/resources/source-parent/source",
        "/local/acct/resources/source-parent/source/empty",
        "/local/acct/resources/target-parent",
    }
    agfs.files = {
        "/local/acct/resources/source-parent/source/data.bin": b"data",
    }
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(fs, "_copy_vector_store_uris", AsyncMock(), raising=False)

    await fs.cp(
        "viking://resources/source-parent/source",
        "viking://resources/target-parent/target",
        recursive=True,
        ctx=_ctx(),
    )

    acquire = next(event for event in agfs.events if event[0] == "acquire-batch")
    assert acquire[1] == [
        {"path": "/local/acct/resources/source-parent", "kind": "tree"},
        {"path": "/local/acct/resources/target-parent", "kind": "tree"},
    ]
    assert agfs.exact_leases == []


@pytest.mark.asyncio
async def test_cp_rejects_existing_target_without_copying(monkeypatch):
    agfs = _CopyAGFS(target_exists=True)
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(fs, "_copy_vector_store_uris", AsyncMock(), raising=False)

    with pytest.raises(ConflictError, match="target already exists"):
        await fs.cp(
            "viking://resources/source.md",
            "viking://resources/target.md",
            ctx=_ctx(),
        )

    assert not any(event[0] == "cp" for event in agfs.events)


@pytest.mark.asyncio
async def test_cp_rejects_missing_target_parent_before_locking(monkeypatch):
    agfs = _CopyAGFS(parent_exists=False)
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(fs, "_copy_vector_store_uris", AsyncMock(), raising=False)

    with pytest.raises(NotFoundError) as exc_info:
        await fs.cp(
            "viking://resources/source.md",
            "viking://resources/target.md",
            ctx=_ctx(),
        )

    assert exc_info.value.message == "Directory not found: viking://resources"
    assert exc_info.value.details == {
        "resource": "viking://resources",
        "type": "directory",
    }
    assert not any(event[0] == "acquire-batch" for event in agfs.events)


@pytest.mark.asyncio
async def test_mv_rejects_missing_target_parent_before_locking(monkeypatch):
    agfs = _CopyAGFS(parent_exists=False)
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(fs, "_update_vector_store_uris", AsyncMock())

    with pytest.raises(NotFoundError) as exc_info:
        await fs.mv(
            "viking://resources/source.md",
            "viking://resources/target.md",
            ctx=_ctx(),
        )

    assert exc_info.value.message == "Directory not found: viking://resources"
    assert exc_info.value.details == {
        "resource": "viking://resources",
        "type": "directory",
    }
    assert not any(event[0] == "acquire-batch" for event in agfs.events)


@pytest.mark.asyncio
async def test_mv_removes_partial_target_when_agfs_copy_fails(monkeypatch):
    agfs = _CopyAGFS(fail_copy=True)
    fs = _viking_fs(monkeypatch, agfs)
    vector_move = AsyncMock()
    monkeypatch.setattr(fs, "_update_vector_store_uris", vector_move)

    with pytest.raises(RuntimeError, match="injected AGFS copy failure"):
        await fs.mv(
            "viking://resources/source.md",
            "viking://resources/target.md",
            ctx=_ctx(),
        )

    assert any(
        event[0:3] == ("rm", "/local/acct/resources/target.md", False) for event in agfs.events
    )
    assert not agfs.target_exists
    vector_move.assert_not_awaited()


@pytest.mark.asyncio
async def test_cp_removes_target_file_when_vector_copy_fails(monkeypatch):
    agfs = _CopyAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(
        fs,
        "_copy_vector_store_uris",
        AsyncMock(side_effect=RuntimeError("vector copy failed")),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="vector copy failed"):
        await fs.cp(
            "viking://resources/source.md",
            "viking://resources/target.md",
            ctx=_ctx(),
        )

    assert any(
        event[0:3] == ("rm", "/local/acct/resources/target.md", False) for event in agfs.events
    )
    assert not agfs.target_exists


@pytest.mark.asyncio
async def test_cp_removes_partial_target_when_agfs_copy_fails(monkeypatch):
    agfs = _CopyAGFS(fail_copy=True)
    fs = _viking_fs(monkeypatch, agfs)
    vector_copy = AsyncMock()
    monkeypatch.setattr(fs, "_copy_vector_store_uris", vector_copy, raising=False)

    with pytest.raises(RuntimeError, match="injected AGFS copy failure"):
        await fs.cp(
            "viking://resources/source.md",
            "viking://resources/target.md",
            ctx=_ctx(),
        )

    assert any(
        event[0:3] == ("rm", "/local/acct/resources/target.md", False) for event in agfs.events
    )
    assert not agfs.target_exists
    vector_copy.assert_not_awaited()


@pytest.mark.asyncio
async def test_mv_restores_source_and_removes_target_when_source_delete_fails(monkeypatch):
    agfs = _MoveRollbackAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    vector_move = AsyncMock()
    monkeypatch.setattr(fs, "_update_vector_store_uris", vector_move)

    with pytest.raises(RuntimeError, match="injected source delete failure"):
        await fs.mv(
            "viking://resources/source.md",
            "viking://resources/target.md",
            ctx=_ctx(),
        )

    assert agfs.paths == {"/local/acct/resources/source.md"}
    assert vector_move.await_args_list[0].args == (
        "viking://resources/source.md",
        "viking://resources/target.md",
    )
    assert vector_move.await_args_list[1].args == (
        "viking://resources/target.md",
        "viking://resources/source.md",
    )


@pytest.mark.asyncio
async def test_mv_restores_vectors_before_removing_target_when_acl_refresh_fails(monkeypatch):
    agfs = _CopyAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    source_uri = "viking://resources/source.md"
    target_uri = "viking://resources/target.md"
    vector_uris = {source_uri}

    async def move_vectors(old_uri, new_uri, *, recursive, ctx):
        assert recursive is False
        assert ctx == _ctx()
        vector_uris.remove(old_uri)
        vector_uris.add(new_uri)
        agfs.events.append(("move-vectors", old_uri, new_uri))
        return SimpleNamespace(scanned=1, written=1, deleted=1, restored=0, batches=1)

    fs.acl_manager = SimpleNamespace(
        is_enabled=lambda account_id: account_id == "acct",
        refresh_context_subtree=AsyncMock(side_effect=RuntimeError("ACL refresh failed")),
    )
    monkeypatch.setattr(fs, "_update_vector_store_uris", move_vectors)

    with pytest.raises(RuntimeError, match="ACL refresh failed"):
        await fs.mv(source_uri, target_uri, ctx=_ctx())

    assert vector_uris == {source_uri}
    assert not agfs.target_exists
    vector_restore_index = agfs.events.index(("move-vectors", target_uri, source_uri))
    target_cleanup_index = next(
        index
        for index, event in enumerate(agfs.events)
        if event[0:3] == ("rm", "/local/acct/resources/target.md", False)
    )
    assert vector_restore_index < target_cleanup_index


@pytest.mark.asyncio
async def test_mv_keeps_target_when_vector_restore_after_acl_refresh_fails(monkeypatch):
    agfs = _CopyAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    source_uri = "viking://resources/source.md"
    target_uri = "viking://resources/target.md"
    vector_uris = {source_uri}

    async def move_vectors(old_uri, new_uri, *, recursive, ctx):
        assert recursive is False
        assert ctx == _ctx()
        if old_uri == target_uri:
            raise RuntimeError("vector restore failed")
        vector_uris.remove(old_uri)
        vector_uris.add(new_uri)
        return SimpleNamespace(scanned=1, written=1, deleted=1, restored=0, batches=1)

    fs.acl_manager = SimpleNamespace(
        is_enabled=lambda account_id: account_id == "acct",
        refresh_context_subtree=AsyncMock(side_effect=RuntimeError("ACL refresh failed")),
    )
    monkeypatch.setattr(fs, "_update_vector_store_uris", move_vectors)

    with pytest.raises(TransferRollbackError) as exc_info:
        await fs.mv(source_uri, target_uri, ctx=_ctx())

    assert exc_info.value.phase == "vector_restore"
    assert exc_info.value.residual_uri == target_uri
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "ACL refresh failed"
    assert vector_uris == {target_uri}
    assert agfs.target_exists
    assert not any(
        event[0:3] == ("rm", "/local/acct/resources/target.md", False) for event in agfs.events
    )


@pytest.mark.asyncio
async def test_mv_directory_rolls_back_partial_source_delete_under_parent_tree(monkeypatch):
    agfs = _DirectoryMoveRollbackAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    vector_move = AsyncMock()
    monkeypatch.setattr(fs, "_update_vector_store_uris", vector_move)

    with pytest.raises(RuntimeError, match="injected partial source delete failure"):
        await fs.mv(
            "viking://resources/source",
            "viking://resources/target",
            ctx=_ctx(),
        )

    assert agfs.directories == {
        "/local/acct/resources",
        "/local/acct/resources/source",
        "/local/acct/resources/source/empty",
    }
    assert agfs.files == {
        "/local/acct/resources/source/.hidden": b"hidden",
        "/local/acct/resources/source/data.bin": b"\x00\xff",
    }
    acquire = next(event for event in agfs.events if event[0] == "acquire-batch")
    assert acquire[1] == [{"path": "/local/acct/resources", "kind": "tree"}]
    assert agfs.exact_leases == []
    assert vector_move.await_args_list[0].args[:2] == (
        "viking://resources/source",
        "viking://resources/target",
    )
    assert vector_move.await_args_list[1].args[:2] == (
        "viking://resources/target",
        "viking://resources/source",
    )


@pytest.mark.asyncio
async def test_persist_temp_tree_explicitly_enables_same_mount_fast_path(monkeypatch):
    fs = VikingFS.__new__(VikingFS)
    fs._async_agfs = SimpleNamespace(cp=AsyncMock())
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, **_kwargs: f"/local/acct/{uri.removeprefix('viking://')}",
    )
    monkeypatch.setattr(fs, "_pathlock_fs_ctx", lambda *_args, **_kwargs: {"lease_ref": "x"})
    monkeypatch.setattr(fs, "_ensure_parent_dirs", AsyncMock())

    await fs.persist_temp_tree(
        "viking://temp/import-1",
        "viking://resources/doc-1",
        ctx=_ctx(),
        lease_ref={"lease_ref": "x"},
    )

    fs._async_agfs.cp.assert_awaited_once_with(
        "/local/acct/temp/import-1",
        "/local/acct/resources/doc-1",
        recursive=True,
        fs_ctx={"lease_ref": "x"},
        allow_same_mount_fast_path=True,
    )


@pytest.mark.asyncio
async def test_mv_returns_diagnostic_transfer_summary(monkeypatch):
    agfs = _CopyAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(
        fs,
        "_update_vector_store_uris",
        AsyncMock(
            return_value=SimpleNamespace(scanned=2, written=2, deleted=2, restored=0, batches=1)
        ),
    )

    result = await fs.mv(
        "viking://resources/source.md",
        "viking://resources/target.md",
        ctx=_ctx(),
    )

    assert len(result["operation_id"]) == 32
    assert result["operation"] == "move"
    assert result["phase"] == "completed"
    assert result["files_created"] == 1
    assert result["files_deleted"] == 1
    assert result["vectors"]["deleted"] == 2


@pytest.mark.asyncio
async def test_mv_reports_target_residual_when_vector_failure_cleanup_fails(monkeypatch):
    agfs = _CopyAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(
        fs,
        "_update_vector_store_uris",
        AsyncMock(side_effect=RuntimeError("vector move failed")),
    )
    monkeypatch.setattr(
        fs,
        "_cleanup_transfer_target",
        AsyncMock(side_effect=RuntimeError("target cleanup failed")),
    )

    with pytest.raises(TransferRollbackError) as exc_info:
        await fs.mv(
            "viking://resources/source.md",
            "viking://resources/target.md",
            ctx=_ctx(),
        )

    assert exc_info.value.phase == "target_cleanup"
    assert exc_info.value.residual_uri == "viking://resources/target.md"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "vector move failed"


@pytest.mark.asyncio
async def test_mv_reports_source_residual_when_source_restore_fails(monkeypatch):
    agfs = _MoveRollbackAGFS()
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(fs, "_update_vector_store_uris", AsyncMock())
    monkeypatch.setattr(
        fs,
        "_copy_agfs_entry",
        AsyncMock(side_effect=[1, RuntimeError("source restore copy failed")]),
    )

    with pytest.raises(TransferRollbackError) as exc_info:
        await fs.mv(
            "viking://resources/source.md",
            "viking://resources/target.md",
            ctx=_ctx(),
        )

    assert exc_info.value.phase == "source_restore"
    assert exc_info.value.residual_uri == "viking://resources/source.md"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "injected source delete failure"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "target", "source_is_dir"),
    [
        ("viking://resources/source.md", "viking://resources/source.md", False),
        ("viking://resources/source", "viking://resources/source/child", True),
    ],
)
async def test_mv_rejects_same_path_and_source_subtree(monkeypatch, source, target, source_is_dir):
    agfs = _CopyAGFS(source_is_dir=source_is_dir)
    fs = _viking_fs(monkeypatch, agfs)
    monkeypatch.setattr(fs, "_update_vector_store_uris", AsyncMock())

    with pytest.raises(InvalidArgumentError):
        await fs.mv(source, target, ctx=_ctx())

    assert not any(event[0] == "acquire-batch" for event in agfs.events)
