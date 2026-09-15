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
    build_resource_diff_snapshot,
    read_new_manifest,
    read_target_file_snapshot,
    read_target_vector_snapshot,
)
from openviking.storage.resource_rnfv import RequestIntent
from openviking.utils.ingest_options import IngestOptions


class _Ctx:
    account_id = "acct"


class _FakeVikingFS:
    """Minimal VikingFS supporting tree() plus the output-store surface."""

    def __init__(self, tree_entries, files=None):
        self._tree_entries = tree_entries
        self.files = files or {}
        self.tree_calls = []

    async def tree(
        self,
        uri,
        *,
        output="original",
        show_all_hidden=False,
        node_limit=1000,
        level_limit=3,
        ctx=None,
    ):
        self.tree_calls.append({"uri": uri, "node_limit": node_limit, "level_limit": level_limit})
        return list(self._tree_entries)

    # Output-store surface for read_new_manifest via AgfsParseOutputStore.
    def create_temp_uri(self, ctx=None):
        return "viking://temp/n"

    async def ls(self, uri, ctx=None, **kwargs):
        prefix = f"{uri.rstrip('/')}/"
        seen = {}
        for stored in self.files:
            if stored.startswith(prefix):
                rest = stored[len(prefix) :]
                head = rest.split("/", 1)
                name = head[0]
                seen[name] = {"name": name, "uri": f"{prefix}{name}", "isDir": len(head) > 1}
        return list(seen.values())


class _FakeVikingDB:
    def __init__(self, records):
        self._records = records
        self.requested = None
        self.inventory_output_fields = None

    async def get_l2_diff_records_by_uris(self, uris, *, ctx):
        self.requested = list(uris)
        return {u: self._records[u] for u in uris if u in self._records}

    async def get_l2_diff_records_under_uri(self, target_uri, *, ctx):
        self.requested = target_uri
        prefix = target_uri.rstrip("/") + "/"
        return {
            uri: value
            for uri, value in self._records.items()
            if uri == target_uri or uri.startswith(prefix)
        }

    async def get_incremental_inventory_under_uri(
        self, target_uri, *, ctx, output_fields=None
    ):
        del ctx
        self.inventory_output_fields = list(output_fields or [])
        prefix = target_uri.rstrip("/") + "/"
        return {
            str(value.get("id") or f"id-{index}"): {
                key: item
                for key, item in {
                    **value,
                    "id": str(value.get("id") or f"id-{index}"),
                    "uri": uri,
                    "level": int(value.get("level", 2)),
                    "md5": str(value.get("md5") or ""),
                }.items()
                if not output_fields or key in output_fields
            }
            for index, (uri, value) in enumerate(self._records.items())
            if uri == target_uri or uri.startswith(prefix)
        }


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
        files, complete = await read_target_file_snapshot(vfs, "viking://resources/x", ctx=_Ctx())
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
                {
                    "rel_path": "secret",
                    "isDir": True,
                    "access": "denied",
                    "uri": "viking://resources/x/secret",
                },
            ]
        )
        files, complete = await read_target_file_snapshot(vfs, "viking://resources/x", ctx=_Ctx())
        # A permission-hidden subtree means we cannot trust the tree for deletion.
        assert complete is False
        assert "secret" not in files

    async def test_control_sidecars_excluded(self) -> None:
        vfs = _FakeVikingFS(
            [
                {"rel_path": "a.py", "isDir": False, "uri": "viking://resources/x/a.py"},
                {
                    "rel_path": ".abstract.md",
                    "isDir": False,
                    "uri": "viking://resources/x/.abstract.md",
                },
                {"rel_path": "_system", "isDir": True, "uri": "viking://resources/x/_system"},
            ]
        )
        files, complete = await read_target_file_snapshot(vfs, "viking://resources/x", ctx=_Ctx())
        assert set(files) == {"a.py"}
        assert complete is True


@pytest.mark.asyncio
class TestReadTargetVectorSnapshot:
    async def test_reads_all_l2_vectors_under_target_and_maps_relative_paths(self) -> None:
        from openviking.storage.viking_fs._diff_plan import TargetVector

        vikingdb = _FakeVikingDB(
            {
                "viking://resources/x/a.py": {"md5": "m1", "abstract": "A"},
                "viking://resources/x/sub/b.py": {"md5": "m2", "abstract": "B"},
                "viking://resources/x/ghost.py": {"md5": "m3", "abstract": "G"},
                "viking://resources/x/a.py#chunk_0001": {
                    "md5": "chunk",
                    "abstract": "chunk summary",
                },
            }
        )
        vectors = await read_target_vector_snapshot(
            vikingdb,
            target_uri="viking://resources/x",
            ctx=_Ctx(),
        )
        assert vectors["a.py"] == TargetVector(md5="m1", abstract="A")
        assert vectors["sub/b.py"].md5 == "m2"
        assert vectors["ghost.py"].md5 == "m3"
        assert vectors["a.py#chunk_0001"].md5 == "chunk"
        assert vikingdb.requested == "viking://resources/x"

    async def test_rejects_noncanonical_vector_uri(self) -> None:
        vikingdb = _FakeVikingDB({"viking://resources/x//a.py": {"md5": "m1", "abstract": "A"}})

        with pytest.raises(RuntimeError, match="non-canonical L2 URI"):
            await read_target_vector_snapshot(
                vikingdb, target_uri="viking://resources/x", ctx=_Ctx()
            )

    async def test_maps_directory_root_l2_record_for_orphan_cleanup(self) -> None:
        vikingdb = _FakeVikingDB({"viking://resources/x": {"md5": "m1", "abstract": "invalid"}})

        vectors = await read_target_vector_snapshot(
            vikingdb, target_uri="viking://resources/x", ctx=_Ctx()
        )

        assert vectors[""].md5 == "m1"


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
        assert set(manifest) == {"a.py", "sub", "sub/b.py"}
        assert manifest["sub"].is_dir is True
        assert manifest["a.py"].is_dir is False
        # No artifact manifest present -> md5 unknown, diff falls back to bytes.
        assert manifest["a.py"].md5 == ""


@pytest.mark.asyncio
async def test_resource_diff_snapshot_reuses_all_level_inventory_for_l2_diff(tmp_path):
    from openviking.parse.output import LocalParseOutputStore

    root = "viking://resources/x"
    store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repository/a.py", b"a")
    vfs = _FakeVikingFS([{"rel_path": "a.py", "isDir": False, "uri": f"{root}/a.py"}])
    vikingdb = _FakeVikingDB(
        {
            root: {"id": "root-l0", "level": 0},
            f"{root}/a.py": {"id": "a-l2", "level": 2, "md5": ""},
        }
    )

    snapshot = await build_resource_diff_snapshot(
        viking_fs=vfs,
        vikingdb=vikingdb,
        store=store,
        artifact_ref=ref,
        target_uri=root,
        ctx=_Ctx(),
        doc_rel="repository",
    )

    assert set(snapshot.vector_inventory) == {"root-l0", "a-l2"}
    assert snapshot.plan.needs_body_compare == ["a.py"]
    assert set(vikingdb.inventory_output_fields) == {"id", "uri", "level", "md5"}


@pytest.mark.asyncio
async def test_resource_diff_snapshot_projects_tags_only_when_request_needs_them(tmp_path):
    from openviking.parse.output import LocalParseOutputStore
    from openviking.parse.parsers.upload_utils import ARTIFACT_MANIFEST_NAME

    root = "viking://resources/x"
    store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repository/a.py", b"a")
    await store.write_text(ref, ARTIFACT_MANIFEST_NAME, json.dumps({"repository/a.py": "m"}))
    vfs = _FakeVikingFS([{"rel_path": "a.py", "isDir": False, "uri": f"{root}/a.py"}])
    vikingdb = _FakeVikingDB(
        {f"{root}/a.py": {"id": "a-l2", "level": 2, "md5": "m", "search_tags": ["env=test"]}}
    )
    request = RequestIntent.from_ingest_options(
        target_uri=root,
        processing_mode="semantic_and_vectors",
        ingest_options=IngestOptions(search_tags=["team=search"], search_tag_mode="append"),
    )

    snapshot = await build_resource_diff_snapshot(
        viking_fs=vfs,
        vikingdb=vikingdb,
        store=store,
        artifact_ref=ref,
        target_uri=root,
        ctx=_Ctx(),
        doc_rel="repository",
        request_intent=request,
    )

    assert "search_tags" in vikingdb.inventory_output_fields
    assert snapshot.rnfv.vectors.projected_fields == request.required_vector_fields()
    assert snapshot.plan.scalar_updates[0].fields == {
        "search_tags": ["env=test", "team=search"]
    }


@pytest.mark.asyncio
async def test_resource_diff_snapshot_without_vector_output_compares_existing_file_body(tmp_path):
    from openviking.parse.output import LocalParseOutputStore

    root = "viking://resources/x"
    store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repository/a.py", b"a")
    vfs = _FakeVikingFS([{"rel_path": "a.py", "isDir": False, "uri": f"{root}/a.py"}])
    vikingdb = _FakeVikingDB({})

    snapshot = await build_resource_diff_snapshot(
        viking_fs=vfs,
        vikingdb=vikingdb,
        store=store,
        artifact_ref=ref,
        target_uri=root,
        ctx=_Ctx(),
        doc_rel="repository",
        require_vectors=False,
    )

    assert snapshot.plan.repair == []
    assert snapshot.plan.needs_body_compare == ["a.py"]

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
