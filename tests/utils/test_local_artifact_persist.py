# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end-ish test for the local-artifact persist branch.

Rather than faking the whole process_resource pipeline (tree/stat/pathlock), this
exercises the actual production method resource_processor uses to land a local
parse artifact into the AGFS resource tree, pinning the key invariant: a local
artifact is uploaded file-by-file to the final resource location, matching source
bytes, without going through persist_temp_tree or any AGFS temp write.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.storage.context_update_plan import ContentTreeAction, ContextUpdatePlan
from openviking.utils.content_hash import content_md5
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

    async def mkdir(self, uri, exist_ok=False, ctx=None, lease_ref=None):
        del exist_ok, ctx, lease_ref

    async def read_file_bytes(self, uri, *, ctx=None):
        return self.files[uri]

    async def remove_files(self, uri, *, recursive=False, ctx=None, lease_ref=None):
        self.files.pop(uri, None)


@pytest.mark.asyncio
async def test_commit_local_artifact_uploads_to_resource_tree(tmp_path, monkeypatch):
    import json

    from openviking.parse.parsers.upload_utils import ARTIFACT_MANIFEST_NAME
    from openviking.utils.content_hash import content_md5

    # A local artifact laid out as <root>/repository/<files>, as code.parse writes.
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "repository/a.py", b"print('a')")
    await store.write_bytes(ref, "repository/src/b.py", b"print('b')")
    # The manifest sidecar is the md5 source of truth (written by upload_directory).
    await store.write_bytes(
        ref,
        ARTIFACT_MANIFEST_NAME,
        json.dumps(
            {
                "repository/a.py": content_md5(b"print('a')"),
                "repository/src/b.py": content_md5(b"print('b')"),
            }
        ).encode("utf-8"),
    )

    agfs = _RecordingAgfs()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: agfs)

    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)

    # temp_doc_uri is <artifact_root>/repository (finalize builds it this way).
    doc_rel = rp._artifact_doc_rel(ref, f"{ref.root}/repository")
    assert doc_rel == "repository"

    plan = ContextUpdatePlan(
        root_uri="viking://resources/acme/demo",
        context_type="resource",
        content_tree_actions=(
            ContentTreeAction(
                "upsert",
                "a.py",
                new_kind="file",
                artifact_path="a.py",
                md5=content_md5(b"print('a')"),
            ),
            ContentTreeAction(
                "upsert",
                "src/b.py",
                new_kind="file",
                artifact_path="src/b.py",
                md5=content_md5(b"print('b')"),
            ),
        ),
    )
    snapshot = SimpleNamespace(
        new=SimpleNamespace(entries={}),
        formal=SimpleNamespace(entries={}),
        vectors=SimpleNamespace(records_by_id={}),
    )
    monkeypatch.setattr(
        "openviking.storage.resource_diff.build_rnfv_snapshot", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        "openviking.storage.context_update_plan.build_context_update_plan_from_snapshot",
        AsyncMock(return_value=(SimpleNamespace(entries={}), plan)),
    )

    committed = await rp._commit_directory_artifact_with_plan(
        output_store=store,
        artifact_ref=ref,
        doc_rel=doc_rel,
        root_uri="viking://resources/acme/demo",
        target_preexisting=False,
        ctx=SimpleNamespace(account_id="test-account"),
        lease_ref=None,
        vectorize=True,
        summarize=False,
        processing_mode="semantic_and_vectors",
        is_code_repo=True,
        ingest_options=None,
        source_metadata=None,
    )

    # Files landed under the resource root with the repository prefix stripped,
    # bytes intact, and no temp-tree persistence / AGFS temp allocation occurred.
    assert agfs.files["viking://resources/acme/demo/a.py"] == b"print('a')"
    assert agfs.files["viking://resources/acme/demo/src/b.py"] == b"print('b')"
    assert agfs.persist_temp_tree_calls == 0
    assert agfs.created_temp_uris == 0
    assert committed.content_tree_actions == ()


@pytest.mark.asyncio
async def test_commit_local_flat_file_uploads_exact_target(tmp_path, monkeypatch):
    store = LocalParseOutputStore(local_root=str(tmp_path / "artifacts"))
    ref = await store.create_artifact(root_type="dir")
    await store.write_bytes(ref, "document/report.md", b"report")
    agfs = _RecordingAgfs()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: agfs)

    plan = ContextUpdatePlan(
        root_uri="viking://resources/report.md",
        context_type="resource",
        content_tree_actions=(
            ContentTreeAction(
                "upsert",
                "",
                new_kind="file",
                artifact_path="",
                md5=content_md5(b"report"),
            ),
        ),
    )
    snapshot = SimpleNamespace(
        new=SimpleNamespace(entries={}),
        formal=SimpleNamespace(entries={}),
        vectors=SimpleNamespace(records_by_id={}),
    )
    monkeypatch.setattr(
        "openviking.storage.resource_diff.build_rnfv_snapshot", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        "openviking.storage.context_update_plan.build_context_update_plan_from_snapshot",
        AsyncMock(return_value=(SimpleNamespace(entries={}), plan)),
    )

    result = await ResourceProcessor(
        vikingdb=_DummyVikingDB(), media_storage=None
    )._commit_directory_artifact_with_plan(
        output_store=store,
        artifact_ref=ref,
        doc_rel="document/report.md",
        root_uri="viking://resources/report.md",
        target_preexisting=False,
        root_is_file=True,
        ctx=SimpleNamespace(account_id="test-account"),
        lease_ref=None,
        vectorize=True,
        summarize=False,
        processing_mode="semantic_and_vectors",
        is_code_repo=False,
        ingest_options=None,
        source_metadata=None,
    )

    assert agfs.files == {"viking://resources/report.md": b"report"}
    assert result.content_tree_actions == ()


def test_build_parse_output_store_defaults_to_none(monkeypatch):
    # Default (agfs) config yields no store, so callers keep legacy behaviour.
    rp = ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)
    assert rp._build_parse_output_store() is None


def test_artifact_doc_rel_falls_back_to_ref_metadata(tmp_path):
    from openviking.parse.output import ParseArtifactRef

    ref = ParseArtifactRef(
        backend="local",
        root=str(tmp_path / "artifact"),
        resource_rel="document",
    )

    assert ResourceProcessor._artifact_doc_rel(ref, "not-a-path-below-the-root") == "document"
