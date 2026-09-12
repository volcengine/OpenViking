# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for local-artifact incremental apply into an existing resource tree.

When the target already exists, a local artifact must not be uploaded wholesale:
only added/modified files are written to the AGFS target and removed files are
deleted, decided by the DiffPlan (md5 from the artifact manifest vs md5 in the
vector store). This pins that resource_processor._apply_local_incremental only
touches changed files.
"""

import json

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.upload_utils import ARTIFACT_MANIFEST_NAME
from openviking.utils.content_hash import content_md5
from openviking.utils.resource_processor import ResourceProcessor


class _DummyVikingDB:
    def get_embedder(self):
        return None


class _RecordingAgfs:
    """Fake AGFS resource tree recording writes/deletes; serves existing bytes."""

    def __init__(self, existing):
        self.files = dict(existing)
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

    async def tree(self, uri, *, output="original", show_all_hidden=False,
                   node_limit=1000, level_limit=None, ctx=None):
        base = f"{uri.rstrip('/')}/"
        entries = []
        for stored in self.files:
            if stored.startswith(base):
                rel = stored[len(base):]
                entries.append({"rel_path": rel, "isDir": False, "uri": stored})
        return entries


class _RecordingVikingDB(_DummyVikingDB):
    def __init__(self, records):
        self._records = records
        self.deleted_uris = []

    async def get_l2_diff_records_by_uris(self, uris, *, ctx):
        return {u: self._records[u] for u in uris if u in self._records}

    async def delete_uris(self, ctx, uris):
        self.deleted_uris.extend(uris)


class _Ctx:
    account_id = "acct"


_ROOT = "viking://resources/acme/demo"


async def _artifact(tmp_path, files: dict[str, bytes]):
    """Build a local artifact under repository/ with an md5 manifest."""
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    ref = await store.create_artifact(root_type="dir")
    manifest = {}
    for rel, data in files.items():
        art_rel = f"repository/{rel}"
        await store.write_bytes(ref, art_rel, data)
        manifest[art_rel] = content_md5(data)
    await store.write_text(ref, ARTIFACT_MANIFEST_NAME, json.dumps(manifest))
    return store, ref


@pytest.mark.asyncio
async def test_incremental_noop_uploads_nothing(tmp_path, monkeypatch):
    # Same content as target (matching md5) -> no writes, no deletes.
    store, ref = await _artifact(tmp_path, {"a.py": b"print('a')", "b.py": b"print('b')"})
    agfs = _RecordingAgfs(
        {f"{_ROOT}/a.py": b"print('a')", f"{_ROOT}/b.py": b"print('b')"}
    )
    vikingdb = _RecordingVikingDB(
        {
            f"{_ROOT}/a.py": {"md5": content_md5(b"print('a')"), "abstract": ""},
            f"{_ROOT}/b.py": {"md5": content_md5(b"print('b')"), "abstract": ""},
        }
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: agfs)

    rp = ResourceProcessor(vikingdb=vikingdb, media_storage=None)
    result = await rp._apply_local_incremental(
        output_store=store,
        artifact_ref=ref,
        doc_rel="repository",
        root_uri=_ROOT,
        ctx=_Ctx(),
        lease_ref=None,
    )

    assert agfs.written == []
    assert agfs.removed == []
    assert set(result.unchanged) == {"a.py", "b.py"}
    assert result.files == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_incremental_modified_uploads_only_changed(tmp_path, monkeypatch):
    store, ref = await _artifact(tmp_path, {"a.py": b"print('A2')", "b.py": b"print('b')"})
    agfs = _RecordingAgfs(
        {f"{_ROOT}/a.py": b"print('a')", f"{_ROOT}/b.py": b"print('b')"}
    )
    vikingdb = _RecordingVikingDB(
        {
            f"{_ROOT}/a.py": {"md5": content_md5(b"print('a')"), "abstract": ""},
            f"{_ROOT}/b.py": {"md5": content_md5(b"print('b')"), "abstract": ""},
        }
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: agfs)

    rp = ResourceProcessor(vikingdb=vikingdb, media_storage=None)
    result = await rp._apply_local_incremental(
        output_store=store,
        artifact_ref=ref,
        doc_rel="repository",
        root_uri=_ROOT,
        ctx=_Ctx(),
        lease_ref=None,
    )

    # Only a.py changed -> only a.py uploaded; b.py untouched.
    assert agfs.written == [f"{_ROOT}/a.py"]
    assert agfs.files[f"{_ROOT}/a.py"] == b"print('A2')"
    assert result.uploaded == ["a.py"]
    assert result.files == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_incremental_deletes_removed_file_and_vector(tmp_path, monkeypatch):
    # New artifact drops b.py -> target b.py file and its vector are removed.
    store, ref = await _artifact(tmp_path, {"a.py": b"print('a')"})
    agfs = _RecordingAgfs(
        {f"{_ROOT}/a.py": b"print('a')", f"{_ROOT}/b.py": b"print('b')"}
    )
    vikingdb = _RecordingVikingDB(
        {
            f"{_ROOT}/a.py": {"md5": content_md5(b"print('a')"), "abstract": ""},
            f"{_ROOT}/b.py": {"md5": content_md5(b"print('b')"), "abstract": ""},
        }
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: agfs)

    rp = ResourceProcessor(vikingdb=vikingdb, media_storage=None)
    result = await rp._apply_local_incremental(
        output_store=store,
        artifact_ref=ref,
        doc_rel="repository",
        root_uri=_ROOT,
        ctx=_Ctx(),
        lease_ref=None,
    )

    assert f"{_ROOT}/b.py" in agfs.removed
    assert result.deleted == ["b.py"]


def test_apply_result_to_changes_maps_to_target_uris():
    from openviking.storage.resource_diff_apply import ApplyResult

    result = ApplyResult(
        uploaded=["a.py", "sub/c.py"],
        added=["a.py"],
        modified=["sub/c.py"],
        deleted=["b.py"],
        unchanged=["d.py"],
    )
    changes = ResourceProcessor._apply_result_to_changes(result, _ROOT)

    # Changed/removed files become target URIs; unchanged files are omitted so
    # the DAG reuses their summaries.
    assert changes == {
        "added": [f"{_ROOT}/a.py"],
        "modified": [f"{_ROOT}/sub/c.py"],
        "deleted": [f"{_ROOT}/b.py"],
    }


def test_apply_result_to_changes_empty_when_noop():
    from openviking.storage.resource_diff_apply import ApplyResult

    changes = ResourceProcessor._apply_result_to_changes(
        ApplyResult(unchanged=["a.py", "b.py"]), _ROOT
    )
    assert changes == {}


def test_apply_result_to_changes_reindexes_repair_files():
    from openviking.storage.resource_diff_apply import ApplyResult

    changes = ResourceProcessor._apply_result_to_changes(
        ApplyResult(repair=["missing-vector.py"]), _ROOT
    )

    assert changes == {
        "modified": [f"{_ROOT}/missing-vector.py"],
    }
