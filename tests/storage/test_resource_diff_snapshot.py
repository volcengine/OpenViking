# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for reading the N/F/V snapshots and assembling a DiffPlan.

These cover the IO-facing readers (target file tree, target vectors, new-artifact
manifest) and how completeness is derived. The classification itself is already
pinned by tests/storage/test_resource_sync_plan.py; here we verify the snapshot
plumbing and that permission-hidden / truncated target trees mark the file
snapshot incomplete so the planner refuses deletions.
"""

import json

import pytest

from openviking.parse.output import AgfsParseOutputStore, ParseArtifactRef
from openviking.storage.resource_diff import (
    read_new_manifest,
    read_target_file_snapshot,
    read_target_vector_snapshot,
)


class _Ctx:
    account_id = "acct"


class _FakeVikingFS:
    """Minimal VikingFS supporting tree() plus the output-store surface."""

    def __init__(self, tree_entries, files=None):
        self._tree_entries = tree_entries
        self.files = files or {}
        self.tree_calls = []

    async def tree(self, uri, *, output="original", show_all_hidden=False,
                   node_limit=1000, level_limit=3, ctx=None):
        self.tree_calls.append(
            {"uri": uri, "node_limit": node_limit, "level_limit": level_limit}
        )
        return list(self._tree_entries)

    # Output-store surface for read_new_manifest via AgfsParseOutputStore.
    def create_temp_uri(self, ctx=None):
        return "viking://temp/n"

    async def ls(self, uri, ctx=None, **kwargs):
        prefix = f"{uri.rstrip('/')}/"
        seen = {}
        for stored in self.files:
            if stored.startswith(prefix):
                rest = stored[len(prefix):]
                head = rest.split("/", 1)
                name = head[0]
                seen[name] = {"name": name, "uri": f"{prefix}{name}", "isDir": len(head) > 1}
        return list(seen.values())


class _FakeVikingDB:
    def __init__(self, records):
        self._records = records
        self.requested = None

    async def get_l2_diff_records_by_uris(self, uris, *, ctx):
        self.requested = list(uris)
        return {u: self._records[u] for u in uris if u in self._records}


@pytest.mark.asyncio
class TestReadTargetFileSnapshot:
    async def test_lists_business_files_with_unlimited_scan(self) -> None:
        vfs = _FakeVikingFS(
            [
                {"rel_path": "a.py", "isDir": False, "uri": "viking://resources/x/a.py"},
                {"rel_path": "sub", "isDir": True, "uri": "viking://resources/x/sub"},
                {"rel_path": "sub/b.py", "isDir": False, "uri": "viking://resources/x/sub/b.py"},
            ]
        )
        files, complete = await read_target_file_snapshot(
            vfs, "viking://resources/x", ctx=_Ctx()
        )
        assert complete is True
        assert set(files) == {"a.py", "sub", "sub/b.py"}
        assert files["sub"].is_dir is True
        assert files["a.py"].is_dir is False
        # Must scan the whole tree, not the default bounded window.
        assert vfs.tree_calls[0]["node_limit"] is None
        assert vfs.tree_calls[0]["level_limit"] is None

    async def test_denied_entry_marks_snapshot_incomplete(self) -> None:
        vfs = _FakeVikingFS(
            [
                {"rel_path": "a.py", "isDir": False, "uri": "viking://resources/x/a.py"},
                {"rel_path": "secret", "isDir": True, "access": "denied",
                 "uri": "viking://resources/x/secret"},
            ]
        )
        files, complete = await read_target_file_snapshot(
            vfs, "viking://resources/x", ctx=_Ctx()
        )
        # A permission-hidden subtree means we cannot trust the tree for deletion.
        assert complete is False
        assert "secret" not in files

    async def test_control_sidecars_excluded(self) -> None:
        vfs = _FakeVikingFS(
            [
                {"rel_path": "a.py", "isDir": False, "uri": "viking://resources/x/a.py"},
                {"rel_path": ".abstract.md", "isDir": False,
                 "uri": "viking://resources/x/.abstract.md"},
                {"rel_path": "_system", "isDir": True, "uri": "viking://resources/x/_system"},
            ]
        )
        files, complete = await read_target_file_snapshot(
            vfs, "viking://resources/x", ctx=_Ctx()
        )
        assert set(files) == {"a.py"}
        assert complete is True


@pytest.mark.asyncio
class TestReadTargetVectorSnapshot:
    async def test_maps_relative_paths_to_vectors(self) -> None:
        from openviking.storage.viking_fs._diff_plan import TargetVector

        vikingdb = _FakeVikingDB(
            {
                "viking://resources/x/a.py": {"md5": "m1", "abstract": "A"},
                "viking://resources/x/sub/b.py": {"md5": "m2", "abstract": "B"},
            }
        )
        vectors = await read_target_vector_snapshot(
            vikingdb,
            target_uri="viking://resources/x",
            rel_paths=["a.py", "sub/b.py", "missing.py"],
            ctx=_Ctx(),
        )
        assert vectors["a.py"] == TargetVector(md5="m1", abstract="A")
        assert vectors["sub/b.py"].md5 == "m2"
        assert "missing.py" not in vectors


@pytest.mark.asyncio
class TestReadNewManifest:
    async def test_lists_new_artifact_files_without_md5(self) -> None:
        vfs = _FakeVikingFS(
            [],
            files={
                "viking://temp/n/a.py": b"a",
                "viking://temp/n/sub/b.py": b"b",
            },
        )
        store = AgfsParseOutputStore(viking_fs=vfs)
        ref = ParseArtifactRef(backend="agfs", root="viking://temp/n", root_type="dir")

        manifest = await read_new_manifest(store, ref)
        # Leaf files only; directories are traversed, not emitted as diff keys.
        assert set(manifest) == {"a.py", "sub/b.py"}
        assert manifest["a.py"].is_dir is False
        # No artifact manifest present -> md5 unknown, diff falls back to bytes.
        assert manifest["a.py"].md5 == ""

    async def test_fills_md5_from_artifact_manifest(self, tmp_path) -> None:
        from openviking.parse.output import LocalParseOutputStore
        from openviking.parse.parsers.upload_utils import ARTIFACT_MANIFEST_NAME

        store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "repository/a.py", b"a")
        await store.write_bytes(ref, "repository/sub/b.py", b"b")
        # Manifest keys are artifact-relative (as upload_directory writes them).
        await store.write_text(
            ref,
            ARTIFACT_MANIFEST_NAME,
            json.dumps({"repository/a.py": "md5a", "repository/sub/b.py": "md5b"}),
        )

        manifest = await read_new_manifest(store, ref, doc_rel="repository")

        # doc_rel stripped, md5 taken from the artifact manifest.
        assert set(manifest) == {"a.py", "sub/b.py"}
        assert manifest["a.py"].md5 == "md5a"
        assert manifest["sub/b.py"].md5 == "md5b"

    async def test_missing_manifest_falls_back_to_empty_md5(self, tmp_path) -> None:
        from openviking.parse.output import LocalParseOutputStore

        store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
        ref = await store.create_artifact(root_type="dir")
        await store.write_bytes(ref, "repository/a.py", b"a")

        manifest = await read_new_manifest(store, ref, doc_rel="repository")

        # No manifest sidecar -> md5 unknown, no error raised.
        assert set(manifest) == {"a.py"}
        assert manifest["a.py"].md5 == ""
