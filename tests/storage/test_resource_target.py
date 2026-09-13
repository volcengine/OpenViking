# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the AGFS resource target used by the DiffPlan apply executor.

The target is the concrete write side of apply_diff_plan: it joins artifact
relative paths onto the resource root, applies the same encoding normalization
as directory uploads (so a file's final bytes — and thus its md5 — match between
AGFS and local artifact modes), and deletes files / L2 vectors.
"""

import pytest

from openviking.parse.output import ParseArtifactRef
from openviking.storage.resource_diff_apply import apply_diff_plan
from openviking.storage.resource_target import AgfsResourceTarget
from openviking.storage.viking_fs._diff_plan import DiffPlan


class _Ctx:
    account_id = "acct"


class _FakeVikingFS:
    def __init__(self, existing=None):
        self.files = dict(existing or {})
        self.written = []
        self.removed = []

    async def write_file_bytes(self, uri, content, *, ctx=None, lease_ref=None):
        self.files[uri] = content
        self.written.append(uri)

    async def read_file_bytes(self, uri, *, ctx=None):
        return self.files[uri]

    async def rm(self, uri, *, recursive=False, ctx=None, lease_ref=None):
        self.removed.append(uri)
        self.files.pop(uri, None)


class _FakeVikingDB:
    def __init__(self):
        self.deleted_uris = []

    async def delete_uris(self, ctx, uris):
        self.deleted_uris.extend(uris)


class _FakeStore:
    """Parse output store returning raw (unnormalized) artifact bytes."""

    def __init__(self, files):
        self._files = dict(files)

    async def read_bytes(self, ref, rel_path):
        return self._files[rel_path]


_REF = ParseArtifactRef(backend="local", root="/tmp/art", root_type="dir")
_ROOT = "viking://resources/proj"


def _target(vfs, vikingdb):
    return AgfsResourceTarget(viking_fs=vfs, vikingdb=vikingdb, root_uri=_ROOT, ctx=_Ctx())


@pytest.mark.asyncio
class TestAgfsResourceTarget:
    async def test_write_joins_rel_path_under_root(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs, _FakeVikingDB())

        await target.write_file("sub/a.py", b"print(1)")

        assert vfs.files[f"{_ROOT}/sub/a.py"] == b"print(1)"

    async def test_empty_rel_path_targets_root_file(self) -> None:
        vfs = _FakeVikingFS()
        vikingdb = _FakeVikingDB()
        target = _target(vfs, vikingdb)

        await target.write_file("", b"body")
        await target.delete_vector("")

        assert vfs.files[_ROOT] == b"body"
        assert vikingdb.deleted_uris == [_ROOT]

    async def test_write_normalizes_text_encoding(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs, _FakeVikingDB())

        # A GBK-encoded .py must be normalized to UTF-8 on write, matching the
        # directory upload path so md5 is stable across artifact backends.
        await target.write_file("a.py", "你好".encode("gbk"))

        assert vfs.files[f"{_ROOT}/a.py"] == "你好".encode("utf-8")

    async def test_write_rejects_path_escape(self) -> None:
        vfs = _FakeVikingFS()
        target = _target(vfs, _FakeVikingDB())

        with pytest.raises(ValueError):
            await target.write_file("../evil.py", b"x")

    async def test_delete_file_removes_uri(self) -> None:
        vfs = _FakeVikingFS(existing={f"{_ROOT}/gone.py": b"x"})
        target = _target(vfs, _FakeVikingDB())

        await target.delete_file("gone.py")

        assert f"{_ROOT}/gone.py" in vfs.removed

    async def test_delete_vector_removes_l2_record(self) -> None:
        vikingdb = _FakeVikingDB()
        target = _target(_FakeVikingFS(), vikingdb)

        await target.delete_vector("ghost.py")

        assert vikingdb.deleted_uris == [f"{_ROOT}/ghost.py"]

    async def test_read_file_returns_existing_bytes(self) -> None:
        vfs = _FakeVikingFS(existing={f"{_ROOT}/a.py": b"body"})
        target = _target(vfs, _FakeVikingDB())

        assert await target.read_file("a.py") == b"body"


@pytest.mark.asyncio
class TestApplyThroughAgfsTarget:
    async def test_initial_upload_all_added_normalizes_and_hashes(self) -> None:
        # An initial import is "plan is all added": every file goes through the
        # same target upload path as an incremental subset.
        vfs = _FakeVikingFS()
        store = _FakeStore({"a.py": "你好".encode("gbk"), "b.py": b"plain"})
        target = _target(vfs, _FakeVikingDB())
        plan = DiffPlan(added=["a.py", "b.py"])

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert vfs.files[f"{_ROOT}/a.py"] == "你好".encode("utf-8")
        assert vfs.files[f"{_ROOT}/b.py"] == b"plain"
        # md5 is over the normalized final bytes, matching what AGFS-mode uploads.
        from openviking.utils.content_hash import content_md5

        assert result.md5_by_rel["a.py"] == content_md5("你好".encode("utf-8"))
