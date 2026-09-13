# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end-ish test for the local-artifact persist branch.

Rather than faking the whole process_resource pipeline (tree/stat/pathlock), this
exercises the actual production method resource_processor uses to land a local
parse artifact into the AGFS resource tree, pinning the key invariant: a local
artifact is uploaded file-by-file to the final resource location, matching source
bytes, without going through persist_temp_tree or any AGFS temp write.
"""

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.utils.resource_processor import ResourceProcessor


class _DummyVikingDB:
    def get_embedder(self):
        return None


class _RecordingAgfs:
    """Fake AGFS resource tree recording writes; forbids temp-tree persistence."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.persist_temp_tree_calls = 0
        self.created_temp_uris = 0

    def create_temp_uri(self, ctx=None):
        self.created_temp_uris += 1
        raise AssertionError("local persist must not allocate an AGFS temp uri")

    async def persist_temp_tree(self, *args, **kwargs):
        self.persist_temp_tree_calls += 1
        raise AssertionError("local persist must not call persist_temp_tree")

    async def write_file_bytes(self, uri, content, *, ctx=None, lease_ref=None):
        self.files[uri] = content

    async def read_file_bytes(self, uri, *, ctx=None):
        return self.files[uri]

    async def rm(self, uri, *, recursive=False, ctx=None, lease_ref=None):
        self.files.pop(uri, None)


@pytest.mark.asyncio
async def test_persist_local_artifact_uploads_to_resource_tree(tmp_path, monkeypatch):
    # A local artifact laid out as <root>/repository/<files>, as code.parse writes.
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repository/a.py", b"print('a')")
    await store.write_bytes(ref, "repository/src/b.py", b"print('b')")

    agfs = _RecordingAgfs()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: agfs)

    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)

    # temp_doc_uri is <artifact_root>/repository (finalize builds it this way).
    doc_rel = rp._artifact_doc_rel(ref, f"{ref.root}/repository")
    assert doc_rel == "repository"

    apply_result = await rp._persist_local_artifact(
        output_store=store,
        artifact_ref=ref,
        doc_rel=doc_rel,
        root_uri="viking://resources/acme/demo",
        ctx=object(),
        lease_ref=None,
    )

    # Files landed under the resource root with the repository prefix stripped,
    # bytes intact, and no temp-tree persistence / AGFS temp allocation occurred.
    assert agfs.files["viking://resources/acme/demo/a.py"] == b"print('a')"
    assert agfs.files["viking://resources/acme/demo/src/b.py"] == b"print('b')"
    assert agfs.persist_temp_tree_calls == 0
    assert agfs.created_temp_uris == 0
    assert apply_result.uploaded == ["a.py", "src/b.py"]
    assert apply_result.files == ["a.py", "src/b.py"]
    assert set(apply_result.md5_by_rel) == {"a.py", "src/b.py"}


@pytest.mark.asyncio
async def test_persist_local_flat_file_uploads_exact_target(tmp_path, monkeypatch):
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "document/report.md", b"report")
    agfs = _RecordingAgfs()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: agfs)

    result = await ResourceProcessor(
        vikingdb=_DummyVikingDB(), media_storage=None
    )._persist_local_artifact(
        output_store=store,
        artifact_ref=ref,
        doc_rel="document/report.md",
        root_uri="viking://resources/report.md",
        root_is_file=True,
        ctx=object(),
        lease_ref=None,
    )

    assert agfs.files == {"viking://resources/report.md": b"report"}
    assert result.files == [""]


def test_build_parse_output_store_defaults_to_none(monkeypatch):
    # Default (agfs) config yields no store, so callers keep legacy behaviour.
    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    assert rp._build_parse_output_store() is None
