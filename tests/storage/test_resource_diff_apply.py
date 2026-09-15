# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for applying a DiffPlan against a target via a backend-agnostic writer.

The executor reads new bytes from the parse output store, uploads only changed
files to the target (computing md5 at the upload point), deletes removed files
and orphan vectors, and leaves unchanged files untouched. It is pure w.r.t. the
store/target interfaces so both AGFS and local backends reuse it.
"""

import asyncio

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
        self.created_dirs = []

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

    async def mkdir(self, rel_path):
        self.created_dirs.append(rel_path)


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
        # md5 is the source-of-truth manifest fingerprint carried on the plan
        # (computed at the parse-upload site), not re-hashed here.
        plan = DiffPlan(
            added=["a.py"],
            modified=["b.py"],
            new_md5s={"a.py": content_md5(b"aaa"), "b.py": content_md5(b"bbb")},
        )
        store = _FakeStore({"a.py": b"aaa", "b.py": b"bbb"})
        target = _FakeTarget(existing={"b.py": b"old"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert target.written == {"a.py": b"aaa", "b.py": b"bbb"}
        assert set(result.uploaded) == {"a.py", "b.py"}
        assert result.added == ["a.py"]
        assert result.modified == ["b.py"]
        # md5 comes from the plan manifest so it can feed embedding.
        assert result.md5_by_rel["a.py"] == content_md5(b"aaa")
        assert result.md5_by_rel["b.py"] == content_md5(b"bbb")

    async def test_deleted_and_orphans_removed(self) -> None:
        plan = DiffPlan(deleted=["gone.py"], orphan_vectors=["ghost.py"])
        store = _FakeStore({})
        target = _FakeTarget(existing={"gone.py": b"x"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert target.deleted_files == ["gone.py"]
        assert target.deleted_vectors == ["gone.py", "ghost.py"]
        assert result.deleted == ["gone.py"]

    async def test_file_tree_only_apply_defers_vector_deletes(self) -> None:
        plan = DiffPlan(
            deleted=["gone.py"],
            orphan_vectors=["ghost.py"],
            structural=["replaced"],
        )
        target = _FakeTarget(existing={"gone.py": b"x", "replaced": b"old"})

        result = await apply_diff_plan(
            plan,
            store=_FakeStore({}),
            artifact_ref=_REF,
            target=target,
            delete_vectors=False,
        )

        assert target.deleted_files == ["replaced", "gone.py"]
        assert target.deleted_vectors == []
        assert result.orphan_vectors == ["ghost.py"]

    async def test_deleted_directories_are_removed_deepest_first(self) -> None:
        plan = DiffPlan(deleted_dirs=["old", "old/nested"])
        target = _FakeTarget()

        result = await apply_diff_plan(
            plan,
            store=_FakeStore({}),
            artifact_ref=_REF,
            target=target,
            delete_vectors=False,
        )

        assert target.deleted_files == ["old"]
        assert result.deleted_dirs == ["old"]

    async def test_added_directories_are_created_shallowest_first(self) -> None:
        plan = DiffPlan(added_dirs=["src/nested", "src"])
        target = _FakeTarget()

        result = await apply_diff_plan(
            plan,
            store=_FakeStore({}),
            artifact_ref=_REF,
            target=target,
            delete_vectors=False,
        )

        assert target.created_dirs == ["src", "src/nested"]
        assert result.added_dirs == ["src", "src/nested"]

    async def test_needs_body_compare_equal_is_unchanged(self) -> None:
        plan = DiffPlan(needs_body_compare=["a.py"])
        store = _FakeStore({"a.py": b"same"})
        target = _FakeTarget(existing={"a.py": b"same"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert result.unchanged == ["a.py"]
        assert target.written == {}

    async def test_needs_body_compare_different_uploads(self) -> None:
        plan = DiffPlan(
            needs_body_compare=["a.py"],
            new_md5s={"a.py": content_md5(b"new")},
        )
        store = _FakeStore({"a.py": b"new"})
        target = _FakeTarget(existing={"a.py": b"old"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert result.uploaded == ["a.py"]
        assert result.modified == ["a.py"]
        assert target.written == {"a.py": b"new"}
        # md5 comes from the plan manifest for the resolved body-compare upload.
        assert result.md5_by_rel["a.py"] == content_md5(b"new")

    async def test_repair_with_different_body_uploads_and_keeps_repair_state(self) -> None:
        plan = DiffPlan(repair=["a.py"], new_md5s={"a.py": content_md5(b"new")})
        target = _FakeTarget(existing={"a.py": b"old"})

        result = await apply_diff_plan(
            plan,
            store=_FakeStore({"a.py": b"new"}),
            artifact_ref=_REF,
            target=target,
        )

        assert target.written == {"a.py": b"new"}
        assert result.repair == ["a.py"]
        # md5 comes from the plan manifest for the re-uploaded repair file.
        assert result.md5_by_rel["a.py"] == content_md5(b"new")

    async def test_repair_with_same_body_skips_upload_but_keeps_repair_state(self) -> None:
        plan = DiffPlan(repair=["a.py"])
        target = _FakeTarget(existing={"a.py": b"same"})

        result = await apply_diff_plan(
            plan,
            store=_FakeStore({"a.py": b"same"}),
            artifact_ref=_REF,
            target=target,
        )

        assert target.written == {}
        assert result.repair == ["a.py"]

    async def test_structural_replaces_by_delete_then_write(self) -> None:
        # A path that flipped file<->dir must be removed before the new node is
        # written, so stale content never lingers under the same URI.
        plan = DiffPlan(structural=["a"], added=["a"])
        store = _FakeStore({"a": b"newfile"})
        target = _FakeTarget(existing={"a": b"olddir-marker"})

        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert "a" in target.deleted_files
        assert "a" in target.deleted_vectors
        assert target.written["a"] == b"newfile"
        assert "a" in result.structural

    async def test_failed_upload_reports_and_stops(self) -> None:
        class _FailingTarget(_FakeTarget):
            async def write_file(self, rel_path, data):
                raise IOError("upload boom")

        plan = DiffPlan(added=["a.py"])
        store = _FakeStore({"a.py": b"a"})
        target = _FailingTarget()

        with pytest.raises(RuntimeError, match=r"failed to upload a\.py: upload boom"):
            await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

    async def test_failed_upload_waits_for_active_writes_and_preserves_error(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "openviking.parse.parsers.upload_utils._UPLOAD_CONCURRENCY", 2
        )
        healthy_started = asyncio.Event()
        healthy_release = asyncio.Event()
        failure_raised = asyncio.Event()
        writes = []
        failure = IOError("upload boom: bad.py")

        class _CoordinatedTarget(_FakeTarget):
            async def write_file(self, rel_path, data):
                if rel_path == "bad.py":
                    await healthy_started.wait()
                    failure_raised.set()
                    raise failure
                if rel_path == "active.py":
                    healthy_started.set()
                    await healthy_release.wait()
                    writes.append(rel_path)
                    return
                writes.append(rel_path)

        task = asyncio.create_task(
            apply_diff_plan(
                DiffPlan(added=["bad.py", "active.py", "pending.py"]),
                store=_FakeStore(
                    {"bad.py": b"bad", "active.py": b"active", "pending.py": b"pending"}
                ),
                artifact_ref=_REF,
                target=_CoordinatedTarget(),
            )
        )

        await asyncio.wait_for(failure_raised.wait(), timeout=1)
        # The first error is known, but the caller must not regain control until
        # the write that was already issued has settled. The queued third write
        # must never start.
        await asyncio.sleep(0)
        assert not task.done()
        assert writes == []

        healthy_release.set()
        with pytest.raises(
            RuntimeError, match=r"failed to upload bad\.py: upload boom: bad\.py"
        ) as exc_info:
            await task
        assert exc_info.value.__cause__ is failure
        assert writes == ["active.py"]

        # Once the exception reaches the caller, no sibling remains that can
        # mutate the target after cleanup or pathlock release.
        await asyncio.sleep(0.05)
        assert writes == ["active.py"]

    async def test_added_modified_uploads_run_concurrently(self) -> None:
        # added/modified are independent remote writes and must overlap; a barrier
        # that only releases once every write is inflight would deadlock a serial
        # loop.
        files = {f"f{i}.py": f"c{i}".encode() for i in range(6)}
        plan = DiffPlan(
            added=list(files),
            new_md5s={name: content_md5(data) for name, data in files.items()},
        )
        store = _FakeStore(files)

        started = asyncio.Event()
        inflight = 0
        peak = 0

        class _BarrierTarget(_FakeTarget):
            async def write_file(self, rel_path, data):
                nonlocal inflight, peak
                inflight += 1
                peak = max(peak, inflight)
                if inflight >= len(files):
                    started.set()
                await asyncio.wait_for(started.wait(), timeout=5)
                inflight -= 1
                await super().write_file(rel_path, data)

        target = _BarrierTarget()
        result = await apply_diff_plan(plan, store=store, artifact_ref=_REF, target=target)

        assert peak == len(files)
        assert set(result.uploaded) == set(files)
        for name, data in files.items():
            assert result.md5_by_rel[name] == content_md5(data)
