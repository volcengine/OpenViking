# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for reading N/F/V snapshots and preparing artifact inventory."""

import asyncio
import json

import pytest

from openviking.parse.output import AgfsParseOutputStore, ParseArtifactRef
from openviking.storage.resource_diff import (
    build_rnfv_snapshot,
    prepare_artifact_inventory,
    read_target_file_snapshot,
)
from openviking.storage.resource_rnfv import FormalEntry, NewEntry


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

    # Output-store surface for ParseOutputStore-backed artifact snapshots.
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

    async def read(self, uri, **kwargs):
        return self.files[uri]

    async def write_file(self, uri, content, **kwargs):
        self.files[uri] = content.encode("utf-8") if isinstance(content, str) else content


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

    async def get_incremental_inventory_under_uri(self, target_uri, *, ctx, output_fields=None):
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
class TestReadFormalTreeSnapshot:
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
                {
                    "rel_path": ".path.ovlock",
                    "isDir": False,
                    "uri": "viking://resources/x/.path.ovlock",
                },
                {
                    "rel_path": "tasks/.exact.ovlock.t.md.0123abcd",
                    "isDir": False,
                    "uri": "viking://resources/x/tasks/.exact.ovlock.t.md.0123abcd",
                },
                # A user directory named tasks or _system is ordinary business content.
                {
                    "rel_path": "tasks/t.md",
                    "isDir": False,
                    "uri": "viking://resources/x/tasks/t.md",
                },
                {
                    "rel_path": "_system/s.md",
                    "isDir": False,
                    "uri": "viking://resources/x/_system/s.md",
                },
            ]
        )
        files, complete = await read_target_file_snapshot(vfs, "viking://resources/x", ctx=_Ctx())
        assert set(files) == {"a.py", "tasks/t.md", "_system/s.md"}
        assert complete is True


@pytest.mark.asyncio
async def test_prepare_artifact_inventory_rewrites_images_during_single_artifact_walk(tmp_path):
    from unittest.mock import AsyncMock

    from openviking.parse.output import LocalParseOutputStore
    from openviking.utils.content_hash import content_md5

    store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repository/docs/guide.md", b"![diagram](diagram.png)\n")
    await store.write_bytes(ref, "repository/docs/diagram.png", b"png")
    await store.write_text(
        ref,
        "repository/.image_mappings.json",
        json.dumps({"docs/guide.md": {"diagram.png": "diagram.png"}}),
    )
    store.list = AsyncMock(wraps=store.list)

    inventory = await prepare_artifact_inventory(
        store,
        ref,
        doc_rel="repository",
        target_root_uri="viking://resources/repo",
    )

    assert set(inventory.entries) == {"docs", "docs/guide.md", "docs/diagram.png"}
    assert inventory.entries["docs/guide.md"].md5 == content_md5(
        b"![diagram](viking://resources/repo/docs/diagram.png)\n"
    )
    assert inventory.artifact_paths["docs/guide.md"] == "repository/docs/guide.md"
    assert inventory.rewritten_paths == frozenset({"docs/guide.md"})
    assert [call.args[1] for call in store.list.await_args_list] == [
        "repository",
        "repository/docs",
    ]


@pytest.mark.asyncio
async def test_prepare_artifact_inventory_rewrites_images_in_agfs_artifact():
    from openviking.utils.content_hash import content_md5

    vfs = _FakeVikingFS(
        [],
        files={
            "viking://temp/n/repository/docs/guide.md": b"![diagram](diagram.png)\n",
            "viking://temp/n/repository/docs/diagram.png": b"png",
            "viking://temp/n/repository/.image_mappings.json": json.dumps(
                {"docs/guide.md": {"diagram.png": "diagram.png"}}
            ).encode(),
        },
    )
    store = AgfsParseOutputStore(viking_fs=vfs)
    ref = ParseArtifactRef(backend="agfs", root="viking://temp/n", root_type="dir")

    inventory = await prepare_artifact_inventory(
        store,
        ref,
        doc_rel="repository",
        target_root_uri="viking://resources/repo",
    )

    expected = b"![diagram](viking://resources/repo/docs/diagram.png)\n"
    assert vfs.files["viking://temp/n/repository/docs/guide.md"] == expected
    assert inventory.entries["docs/guide.md"].md5 == content_md5(expected)


@pytest.mark.asyncio
async def test_build_rnfv_snapshot_reuses_prepared_artifact_inventory(tmp_path):
    from unittest.mock import AsyncMock

    from openviking.parse.output import LocalParseOutputStore

    root = "viking://resources/x"
    store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repository/a.py", b"a")
    store.list = AsyncMock(wraps=store.list)
    inventory = await prepare_artifact_inventory(store, ref, doc_rel="repository")
    store.list.reset_mock()

    snapshot = await build_rnfv_snapshot(
        viking_fs=_FakeVikingFS([]),
        vikingdb=_FakeVikingDB({}),
        store=store,
        artifact_ref=ref,
        target_uri=root,
        ctx=_Ctx(),
        doc_rel="repository",
        artifact_inventory=inventory,
        target_preexisting=False,
    )

    assert set(snapshot.new.entries) == {"a.py"}
    store.list.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_rnfv_snapshot_skips_vector_inventory_when_vectorize_disabled(tmp_path):
    from unittest.mock import AsyncMock

    from openviking.parse.output import LocalParseOutputStore
    from openviking.storage.resource_rnfv import RequestIntent
    from openviking.utils.ingest_options import IngestOptions

    root = "viking://resources/x"
    store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repository/a.py", b"new")
    inventory = await prepare_artifact_inventory(store, ref, doc_rel="repository")
    vikingdb = _FakeVikingDB({f"{root}/a.py": {"id": "stale-l2", "level": 2, "md5": "new"}})
    vikingdb.get_incremental_inventory_under_uri = AsyncMock(
        side_effect=AssertionError("build_index=false must not read vector inventory")
    )

    snapshot = await build_rnfv_snapshot(
        viking_fs=_FakeVikingFS([{"rel_path": "a.py", "isDir": False, "uri": f"{root}/a.py"}]),
        vikingdb=vikingdb,
        store=store,
        artifact_ref=ref,
        target_uri=root,
        ctx=_Ctx(),
        doc_rel="repository",
        request_intent=RequestIntent.from_ingest_options(
            target_uri=root,
            processing_mode="vectors_only",
            ingest_options=IngestOptions.from_search_tags(["team=search"]),
            vectorize=False,
        ),
        artifact_inventory=inventory,
        target_preexisting=True,
    )

    assert snapshot.vectors.records_by_id == {}
    assert snapshot.vectors.projected_fields == frozenset()
    vikingdb.get_incremental_inventory_under_uri.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tags", "tag_mode", "expects_search_tags"),
    [
        (None, "clear", True),
        ([], "replace", False),
    ],
)
async def test_build_rnfv_snapshot_projects_tags_only_for_effective_request_intent(
    tmp_path, tags, tag_mode, expects_search_tags
):
    from openviking.parse.output import LocalParseOutputStore
    from openviking.storage.resource_rnfv import RequestIntent
    from openviking.utils.ingest_options import IngestOptions

    root = "viking://resources/x"
    store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repository/a.py", b"same")
    inventory = await prepare_artifact_inventory(store, ref, doc_rel="repository")
    vikingdb = _FakeVikingDB(
        {
            f"{root}/a.py": {
                "id": "a-l2",
                "level": 2,
                "md5": inventory.entries["a.py"].md5,
                "search_tags": ["scope=old"],
            }
        }
    )
    request = RequestIntent.from_ingest_options(
        target_uri=root,
        processing_mode="vectors_only",
        ingest_options=IngestOptions.from_search_tags(tags, mode=tag_mode),
    )

    snapshot = await build_rnfv_snapshot(
        viking_fs=_FakeVikingFS(
            [{"rel_path": "a.py", "isDir": False, "uri": f"{root}/a.py"}]
        ),
        vikingdb=vikingdb,
        store=store,
        artifact_ref=ref,
        target_uri=root,
        ctx=_Ctx(),
        doc_rel="repository",
        request_intent=request,
        artifact_inventory=inventory,
        target_preexisting=True,
    )

    assert ("search_tags" in vikingdb.inventory_output_fields) is expects_search_tags
    assert ("search_tags" in snapshot.vectors.projected_fields) is expects_search_tags
    record = snapshot.vectors.records_by_id["a-l2"]
    assert ("search_tags" in record.fields) is expects_search_tags


@pytest.mark.asyncio
async def test_build_rnfv_snapshot_starts_new_formal_and_vector_reads_concurrently(
    tmp_path, monkeypatch
):
    from openviking.parse.output import LocalParseOutputStore
    from openviking.storage.resource_diff import ArtifactInventory

    root = "viking://resources/x"
    store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
    ref = await store.create_artifact(root_type="dir")
    started = set()
    release = asyncio.Event()

    async def read_new(*args, **kwargs):
        started.add("new")
        await release.wait()
        return ArtifactInventory(entries={"a.py": NewEntry(md5="new")}, artifact_paths={})

    async def read_formal(*args, **kwargs):
        started.add("formal")
        await release.wait()
        return {"a.py": FormalEntry()}, True

    async def read_vectors(*args, **kwargs):
        started.add("vectors")
        await release.wait()
        return {"record": {"id": "record", "uri": f"{root}/a.py", "level": 2, "md5": "old"}}

    monkeypatch.setattr("openviking.storage.resource_diff.prepare_artifact_inventory", read_new)
    monkeypatch.setattr("openviking.storage.resource_diff.read_target_file_snapshot", read_formal)
    monkeypatch.setattr(
        "openviking.storage.resource_diff._read_incremental_vector_inventory", read_vectors
    )

    task = asyncio.create_task(
        build_rnfv_snapshot(
            viking_fs=_FakeVikingFS([]),
            vikingdb=_FakeVikingDB({}),
            store=store,
            artifact_ref=ref,
            target_uri=root,
            ctx=_Ctx(),
        )
    )
    for _ in range(10):
        if started == {"new", "formal", "vectors"}:
            break
        await asyncio.sleep(0)
    assert started == {"new", "formal", "vectors"}
    release.set()
    snapshot = await task

    assert snapshot.new.entries["a.py"].md5 == "new"
    assert snapshot.formal.entries["a.py"].is_dir is False
    assert snapshot.vectors.records_by_id["record"].fields["md5"] == "old"


@pytest.mark.asyncio
async def test_build_rnfv_snapshot_cancels_sibling_reads_after_failure(tmp_path, monkeypatch):
    from openviking.parse.output import LocalParseOutputStore

    root = "viking://resources/x"
    store = LocalParseOutputStore(local_root=str(tmp_path / "out"))
    ref = await store.create_artifact(root_type="dir")
    started = set()
    cancelled = set()
    fail = asyncio.Event()

    async def read_new(*args, **kwargs):
        started.add("new")
        await fail.wait()
        raise RuntimeError("inventory failed")

    async def wait_for_cancel(name):
        started.add(name)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.add(name)

    monkeypatch.setattr("openviking.storage.resource_diff.prepare_artifact_inventory", read_new)
    monkeypatch.setattr(
        "openviking.storage.resource_diff.read_target_file_snapshot",
        lambda *args, **kwargs: wait_for_cancel("formal"),
    )
    monkeypatch.setattr(
        "openviking.storage.resource_diff._read_incremental_vector_inventory",
        lambda *args, **kwargs: wait_for_cancel("vectors"),
    )

    task = asyncio.create_task(
        build_rnfv_snapshot(
            viking_fs=_FakeVikingFS([]),
            vikingdb=_FakeVikingDB({}),
            store=store,
            artifact_ref=ref,
            target_uri=root,
            ctx=_Ctx(),
        )
    )
    for _ in range(10):
        if started == {"new", "formal", "vectors"}:
            break
        await asyncio.sleep(0)
    fail.set()

    with pytest.raises(RuntimeError, match="inventory failed"):
        await task
    assert cancelled == {"formal", "vectors"}
