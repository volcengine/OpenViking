# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for applying a DiffPlan against a target via a backend-agnostic writer.

The executor reads new bytes from the parse output store, uploads only changed
files to the target (computing md5 at the upload point), deletes removed files
and orphan vectors, and leaves unchanged files untouched. It is pure w.r.t. the
store/target interfaces so both AGFS and local backends reuse it.
"""

import pytest

from openviking.parse.output import ParseArtifactRef
from openviking.storage.resource_diff_apply import apply_diff_plan
from openviking.storage.viking_fs._diff_plan import DiffPlan
from openviking.utils.content_hash import content_md5


class _FakeStore:
    """Parse output store stub keyed by (root, rel_path)."""

    def __init__(self, files):
        self._files = dict(files)

    async def read_bytes(self, ref, rel_path):
        return self._files[rel_path]


class _FakeTarget:
    """Records target-side writes/deletes and serves existing bodies."""

    def __init__(self, existing=None):
        self.existing = dict(existing or {})
        self.written = {}
        self.deleted_files = []
        self.deleted_vectors = []

    async def read_file(self, rel_path):
        return self.existing[rel_path]

    async def write_file(self, rel_path, data):
        self.written[rel_path] = data
        self.existing[rel_path] = data

    async def delete_file(self, rel_path):
        self.deleted_files.append(rel_path)
        self.existing.pop(rel_path, None)

    async def delete_vector(self, rel_path):
        self.deleted_vectors.append(rel_path)


_REF = ParseArtifactRef(backend="agfs", root="viking://temp/n", root_type="dir")


@pytest.mark.asyncio
class TestApplyDiffPlan:
    async def test_unchanged_files_are_not_written(self) -> None:
        plan = DiffPlan(unchanged=["a.py"])
        store = _FakeStore({"a.py": b"a"})
        target = _FakeTarget(existing={"a.py": b"a"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert target.written == {}
        assert result.unchanged == ["a.py"]
        assert result.uploaded == []

    async def test_added_and_modified_upload_with_md5(self) -> None:
        plan = DiffPlan(added=["a.py"], modified=["b.py"])
        store = _FakeStore({"a.py": b"aaa", "b.py": b"bbb"})
        target = _FakeTarget(existing={"b.py": b"old"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert target.written == {"a.py": b"aaa", "b.py": b"bbb"}
        assert set(result.uploaded) == {"a.py", "b.py"}
        # md5 is computed from the uploaded bytes so it can feed embedding.
        assert result.md5_by_rel["a.py"] == content_md5(b"aaa")
        assert result.md5_by_rel["b.py"] == content_md5(b"bbb")

    async def test_deleted_and_orphans_removed(self) -> None:
        plan = DiffPlan(deleted=["gone.py"], orphan_vectors=["ghost.py"])
        store = _FakeStore({})
        target = _FakeTarget(existing={"gone.py": b"x"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert target.deleted_files == ["gone.py"]
        assert target.deleted_vectors == ["ghost.py"]
        assert result.deleted == ["gone.py"]

    async def test_needs_body_compare_equal_is_unchanged(self) -> None:
        plan = DiffPlan(needs_body_compare=["a.py"])
        store = _FakeStore({"a.py": b"same"})
        target = _FakeTarget(existing={"a.py": b"same"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert result.unchanged == ["a.py"]
        assert target.written == {}

    async def test_needs_body_compare_different_uploads(self) -> None:
        plan = DiffPlan(needs_body_compare=["a.py"])
        store = _FakeStore({"a.py": b"new"})
        target = _FakeTarget(existing={"a.py": b"old"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert result.uploaded == ["a.py"]
        assert target.written == {"a.py": b"new"}
        assert result.md5_by_rel["a.py"] == content_md5(b"new")

    async def test_structural_replaces_by_delete_then_write(self) -> None:
        # A path that flipped file<->dir must be removed before the new node is
        # written, so stale content never lingers under the same URI.
        plan = DiffPlan(structural=["a"], added=["a"])
        store = _FakeStore({"a": b"newfile"})
        target = _FakeTarget(existing={"a": b"olddir-marker"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert "a" in target.deleted_files
        assert target.written["a"] == b"newfile"
        assert "a" in result.structural

    async def test_failed_upload_reports_and_stops(self) -> None:
        class _FailingTarget(_FakeTarget):
            async def write_file(self, rel_path, data):
                raise IOError("upload boom")

        plan = DiffPlan(added=["a.py"])
        store = _FakeStore({"a.py": b"a"})
        target = _FailingTarget()

        with pytest.raises(IOError, match="boom"):
            await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)
