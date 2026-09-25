# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the AGFS resource target used by content tree actions."""

from unittest.mock import AsyncMock

import pytest

from openviking.storage.resource_target import AgfsResourceTarget


class _Ctx:
    account_id = "acct"


class _FakeVikingFS:
    def __init__(self, existing=None):
        self.files = dict(existing or {})
        self.written = []
        self.removed = []
        self.ttl_registry = AsyncMock()
        self.ttl_registry.get.return_value = None

    async def write_file_bytes(self, uri, content, *, ctx=None, lease_ref=None):
        self.files[uri] = content
        self.written.append(uri)

    async def read_file_bytes(self, uri, *, ctx=None):
        return self.files[uri]

    async def rm(self, uri, *, recursive=False, ctx=None, lease_ref=None):
        self.removed.append(uri)
        self.files.pop(uri, None)

    async def remove_files(self, uri, *, recursive=False, ctx=None, lease_ref=None):
        self.removed.append((uri, recursive, lease_ref))
        self.files.pop(uri, None)

    async def stat(self, uri, *, ctx=None, skip_count=False):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return {"isDir": False}

    async def _remove_resource_file_metadata(self, uri, *, ctx=None, lease_ref=None):
        self.removed.append((f"metadata:{uri}", False, lease_ref))


_ROOT = "viking://resources/proj"


def _target(vfs):
    return AgfsResourceTarget(viking_fs=vfs, root_uri=_ROOT, ctx=_Ctx())


@pytest.mark.asyncio
class TestAgfsResourceTarget:
    async def test_write_joins_rel_path_under_root(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs)

        await target.write_file("sub/a.py", b"print(1)")

        assert vfs.files[f"{_ROOT}/sub/a.py"] == b"print(1)"

    async def test_empty_rel_path_targets_root_file(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs)

        await target.write_file("", b"body")

        assert vfs.files[_ROOT] == b"body"

    async def test_write_preserves_artifact_bytes(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs)

        artifact_bytes = "你好".encode("gbk")
        await target.write_file("a.py", artifact_bytes)

        assert vfs.files[f"{_ROOT}/a.py"] == artifact_bytes

    async def test_write_rejects_path_escape(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs)

        with pytest.raises(ValueError):
            await target.write_file("../evil.py", b"x")

    async def test_delete_path_uses_exact_semantics_for_file(self) -> None:
        lease = {"lease_ref": "root-tree"}
        vfs = _FakeVikingFS(existing={f"{_ROOT}/gone.py": b"x"})
        target = AgfsResourceTarget(
            viking_fs=vfs,
            root_uri=_ROOT,
            ctx=_Ctx(),
            lease_ref=lease,
        )

        await target.delete_path("gone.py", is_dir=False)

        assert vfs.removed == [(f"{_ROOT}/gone.py", False, lease)]

    async def test_delete_path_uses_tree_semantics_for_directory(self) -> None:
        lease = {"lease_ref": "root-tree"}
        vfs = _FakeVikingFS()
        target = AgfsResourceTarget(
            viking_fs=vfs,
            root_uri=_ROOT,
            ctx=_Ctx(),
            lease_ref=lease,
        )

        await target.delete_path("old-dir", is_dir=True)

        assert vfs.removed == [(f"{_ROOT}/old-dir", True, lease)]

    async def test_ttl_target_removes_file_metadata_with_deleted_source(self) -> None:
        lease = {"lease_ref": "root-tree"}
        vfs = _FakeVikingFS(existing={f"{_ROOT}/gone.py": b"x"})
        target = AgfsResourceTarget(
            viking_fs=vfs,
            root_uri=_ROOT,
            ctx=_Ctx(),
            lease_ref=lease,
            resource_ttl={"ttl_relative": 7},
        )

        await target.delete_path("gone.py", is_dir=False)

        assert vfs.removed == [
            (f"{_ROOT}/gone.py", False, lease),
            (f"metadata:{_ROOT}/gone.py", False, lease),
        ]

    async def test_read_file_returns_existing_bytes(self) -> None:
        vfs = _FakeVikingFS(existing={f"{_ROOT}/a.py": b"body"})
        target = _target(vfs)

        assert await target.read_file("a.py") == b"body"
