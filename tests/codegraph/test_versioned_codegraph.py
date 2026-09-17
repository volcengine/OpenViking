# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

import pytest

from openviking.codegraph import (
    CatalogConflictError,
    CodeGraphBuilder,
    CodeGraphIndex,
    FileAccessScope,
    LocalCodeGraphCatalog,
    SourceFile,
    SupersededRevisionError,
    VersionedCodeGraph,
    stable_file_key,
)

ACCOUNT_ID = "account-1"


def _full_access(repo_id: str = "repo-a") -> FileAccessScope:
    return FileAccessScope.unrestricted(
        account_id=ACCOUNT_ID,
        repo_id=repo_id,
        acl_revision=1,
    )


def _restricted_access(*uris: str, repo_id: str = "repo-a") -> FileAccessScope:
    return FileAccessScope.restricted(
        account_id=ACCOUNT_ID,
        repo_id=repo_id,
        acl_revision=1,
        allowed_file_keys=(stable_file_key(ACCOUNT_ID, repo_id, uri) for uri in uris),
    )


class _MemorySnapshotReader:
    def __init__(self):
        self._snapshots: dict[str, tuple[SourceFile, ...]] = {}

    def seal(self, files: Sequence[SourceFile]) -> str:
        sealed_files = tuple(
            SourceFile(
                relative_path=source.relative_path,
                uri=source.uri,
                content=source.content,
                source_blob_oid=hashlib.sha256(source.content.encode("utf-8")).hexdigest(),
            )
            for source in sorted(files, key=lambda item: item.relative_path)
        )
        inventory = [
            (source.relative_path, source.uri, source.source_blob_oid) for source in sealed_files
        ]
        snapshot_oid = hashlib.sha256(
            json.dumps(inventory, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self._snapshots[snapshot_oid] = sealed_files
        return snapshot_oid

    def read_source_files(self, source_snapshot_oid: str) -> Sequence[SourceFile]:
        try:
            return self._snapshots[source_snapshot_oid]
        except KeyError:
            raise KeyError(f"snapshot not found: {source_snapshot_oid}") from None


def _build(
    root: Path,
    *,
    revision: str,
    commit: str,
    generation: str,
    files: list[SourceFile],
    repo_id: str = "repo-a",
):
    snapshot_reader = _MemorySnapshotReader()
    source_snapshot_oid = snapshot_reader.seal(files)
    return CodeGraphBuilder().build(
        account_id=ACCOUNT_ID,
        repo_id=repo_id,
        revision_id=revision,
        commit_sha=commit,
        source_snapshot_oid=source_snapshot_oid,
        graph_generation=generation,
        snapshot_reader=snapshot_reader,
        output_path=root / f"{generation}.sqlite3",
    )


def test_builder_reads_source_from_requested_snapshot(tmp_path):
    snapshot_reader = _MemorySnapshotReader()
    old_snapshot_oid = snapshot_reader.seal(
        [
            SourceFile(
                relative_path="service.py",
                uri="viking://resources/repo-a/service.py",
                content="def old_api():\n    return 1\n",
            )
        ]
    )
    snapshot_reader.seal(
        [
            SourceFile(
                relative_path="service.py",
                uri="viking://resources/repo-a/service.py",
                content="def new_api():\n    return 2\n",
            )
        ]
    )

    manifest = CodeGraphBuilder().build(
        account_id=ACCOUNT_ID,
        repo_id="repo-a",
        revision_id="revision-1",
        commit_sha="commit-1",
        source_snapshot_oid=old_snapshot_oid,
        graph_generation="graph-1",
        snapshot_reader=snapshot_reader,
        output_path=tmp_path / "graph-1.sqlite3",
    )
    index = CodeGraphIndex(manifest.index_path)

    assert [hit.qualified_name for hit in index.search("old_api", access_scope=_full_access())] == [
        "old_api"
    ]
    assert index.search("new_api", access_scope=_full_access()) == []

    with pytest.raises(KeyError, match="snapshot not found"):
        CodeGraphBuilder().build(
            account_id=ACCOUNT_ID,
            repo_id="repo-a",
            revision_id="revision-missing",
            commit_sha="commit-missing",
            source_snapshot_oid="missing-snapshot",
            graph_generation="graph-missing",
            snapshot_reader=snapshot_reader,
            output_path=tmp_path / "graph-missing.sqlite3",
        )


def test_build_search_and_expand_resolved_call(tmp_path):
    manifest = _build(
        tmp_path,
        revision="revision-1",
        commit="commit-1",
        generation="graph-1",
        files=[
            SourceFile(
                relative_path="greeter.py",
                uri="viking://resources/repo-a/greeter.py",
                content="""\
class Greeter:
    def greet(self, name: str) -> str:
        return helper(name)


def helper(value: str) -> str:
    return value.upper()
""",
            )
        ],
    )

    index = CodeGraphIndex(manifest.index_path)
    hits = index.search("greet", access_scope=_full_access())

    assert hits
    method = next(hit for hit in hits if hit.qualified_name == "Greeter.greet")
    assert method.kind == "method"
    assert method.canonical_signature == "(p:str)->str"
    assert method.start_line == 2

    expansion = index.expand([method.node_id], access_scope=_full_access())

    assert {node.qualified_name for node in expansion.nodes} == {
        "Greeter.greet",
        "helper",
    }
    assert len(expansion.edges) == 1
    assert expansion.edges[0].kind == "calls"
    assert expansion.edges[0].resolution == "resolved"


def test_canonical_signature_distinguishes_overloads(tmp_path):
    manifest = _build(
        tmp_path,
        revision="revision-1",
        commit="commit-1",
        generation="graph-1",
        files=[
            SourceFile(
                relative_path="parser.py",
                uri="viking://resources/repo-a/parser.py",
                content="""\
from typing import overload


@overload
def parse(value: int) -> int: ...


@overload
def parse(value: str) -> str: ...
""",
            )
        ],
    )

    hits = [
        hit
        for hit in CodeGraphIndex(manifest.index_path).search(
            "parse", access_scope=_full_access(), limit=10
        )
        if hit.qualified_name == "parse"
    ]

    assert len(hits) == 2
    assert len({hit.node_id for hit in hits}) == 2
    assert {hit.canonical_signature for hit in hits} == {
        "(p:int)->int",
        "(p:str)->str",
    }


def test_duplicate_local_declarations_use_stable_ordinal(tmp_path):
    manifest = _build(
        tmp_path,
        revision="revision-1",
        commit="commit-1",
        generation="graph-1",
        files=[
            SourceFile(
                relative_path="cleanup.py",
                uri="viking://resources/repo-a/cleanup.py",
                content="""\
def access(use_first: bool):
    if use_first:
        def cleanup():
            return "first"
    else:
        def cleanup():
            return "second"
""",
            )
        ],
    )

    hits = [
        hit
        for hit in CodeGraphIndex(manifest.index_path).search(
            "cleanup", access_scope=_full_access(), limit=10
        )
        if hit.qualified_name == "access.cleanup"
    ]

    assert len(hits) == 2
    assert len({hit.node_id for hit in hits}) == 2
    assert {hit.declaration_ordinal for hit in hits} == {0, 1}


def test_acl_filter_applies_before_fts_and_graph_expansion(tmp_path):
    manifest = _build(
        tmp_path,
        revision="revision-1",
        commit="commit-1",
        generation="graph-1",
        files=[
            SourceFile(
                relative_path="a_public.py",
                uri="viking://resources/repo-a/a_public.py",
                content="""\
def public_entry():
    return private_secret()
""",
            ),
            SourceFile(
                relative_path="z_private.py",
                uri="viking://resources/repo-a/z_private.py",
                content="""\
def private_secret():
    return "secret"
""",
            ),
        ],
    )
    index = CodeGraphIndex(manifest.index_path)
    public_uri = "viking://resources/repo-a/a_public.py"
    public_access = _restricted_access(public_uri)
    deny_all = _restricted_access()

    assert index.search("private_secret", access_scope=_full_access())
    assert index.search("private_secret", access_scope=deny_all) == []
    with pytest.raises(PermissionError, match="does not belong"):
        index.search("private_secret", access_scope=_full_access("repo-b"))
    authorized_hits = index.search("private_secret", access_scope=public_access)
    assert authorized_hits
    assert {hit.file_key for hit in authorized_hits} == {
        stable_file_key(ACCOUNT_ID, "repo-a", public_uri)
    }
    assert "private_secret" not in {hit.qualified_name for hit in authorized_hits}

    unfiltered_public = next(
        hit
        for hit in index.search("public_entry", access_scope=_full_access())
        if hit.qualified_name == "public_entry"
    )
    unfiltered_expansion = index.expand(
        [unfiltered_public.node_id],
        access_scope=_full_access(),
    )
    assert len(unfiltered_expansion.edges) == 1
    assert unfiltered_expansion.edges[0].resolution == "best_effort"

    public = next(
        hit
        for hit in index.search("public_entry", access_scope=public_access)
        if hit.qualified_name == "public_entry"
    )
    expansion = index.expand([public.node_id], access_scope=public_access)

    assert {node.qualified_name for node in expansion.nodes} == {"public_entry"}
    assert expansion.edges == ()


def test_acl_file_key_survives_file_id_renumbering(tmp_path):
    public_uri = "viking://resources/repo-a/a_public.py"
    access_scope = _restricted_access(public_uri)
    graph1 = _build(
        tmp_path,
        revision="revision-1",
        commit="commit-1",
        generation="graph-1",
        files=[
            SourceFile(
                relative_path="a_public.py",
                uri=public_uri,
                content="def public_api():\n    return 1\n",
            )
        ],
    )
    graph2 = _build(
        tmp_path,
        revision="revision-2",
        commit="commit-2",
        generation="graph-2",
        files=[
            SourceFile(
                relative_path="0_private.py",
                uri="viking://resources/repo-a/0_private.py",
                content="def private_api():\n    return 0\n",
            ),
            SourceFile(
                relative_path="a_public.py",
                uri=public_uri,
                content="def public_api():\n    return 2\n",
            ),
        ],
    )

    old_hit = CodeGraphIndex(graph1.index_path).search(
        "public_api",
        access_scope=access_scope,
    )
    new_hit = CodeGraphIndex(graph2.index_path).search(
        "public_api",
        access_scope=access_scope,
    )

    assert [hit.file_id for hit in old_hit] == [1]
    assert [hit.file_id for hit in new_hit] == [2]
    assert old_hit[0].file_key == new_hit[0].file_key
    assert (
        CodeGraphIndex(graph2.index_path).search(
            "private_api",
            access_scope=access_scope,
        )
        == []
    )


def test_catalog_keeps_old_read_view_after_new_revision_is_published(tmp_path):
    manifest1 = _build(
        tmp_path,
        revision="revision-1",
        commit="commit-1",
        generation="graph-1",
        files=[
            SourceFile(
                relative_path="service.py",
                uri="viking://resources/repo-a/service.py",
                content="def old_api():\n    return 1\n",
            )
        ],
    )
    manifest2 = _build(
        tmp_path,
        revision="revision-2",
        commit="commit-2",
        generation="graph-2",
        files=[
            SourceFile(
                relative_path="service.py",
                uri="viking://resources/repo-a/service.py",
                content="def new_api():\n    return 2\n",
            )
        ],
    )
    catalog = LocalCodeGraphCatalog(tmp_path / "catalog.sqlite3", account_id=ACCOUNT_ID)
    service = VersionedCodeGraph(catalog)
    access_scope = _full_access()

    published1 = catalog.publish(manifest1, expected_epoch=0, base_revision_id=None)
    view1 = service.acquire_view()

    assert published1.published_epoch == 1
    assert [
        hit.qualified_name
        for hit in service.search("repo-a", "old_api", access_scope=access_scope)[2]
    ] == ["old_api"]

    # Building graph-2 alone does not change the published catalog.
    assert service.search("repo-a", "new_api", access_scope=access_scope)[2] == []

    published2 = catalog.publish(
        manifest2,
        expected_epoch=1,
        base_revision_id="revision-1",
    )
    latest_view, latest_revision, latest_hits = service.search(
        "repo-a",
        "new_api",
        access_scope=access_scope,
    )
    old_view, old_revision, old_hits = service.search(
        "repo-a",
        "old_api",
        access_scope=access_scope,
        view=view1,
    )

    assert published2.published_epoch == 2
    assert latest_view.epoch == 2
    assert latest_revision.commit_sha == "commit-2"
    assert [hit.qualified_name for hit in latest_hits] == ["new_api"]
    assert old_view == view1
    assert old_revision.commit_sha == "commit-1"
    assert [hit.qualified_name for hit in old_hits] == ["old_api"]

    with pytest.raises(CatalogConflictError, match="expected 1, actual 2"):
        catalog.publish(
            manifest1,
            expected_epoch=1,
            base_revision_id="revision-2",
        )

    rollback = catalog.publish(
        manifest1,
        expected_epoch=2,
        base_revision_id="revision-2",
    )
    rollback_view = catalog.acquire_view()

    assert rollback.published_epoch == 3
    assert catalog.resolve("repo-a", latest_view).commit_sha == "commit-2"
    assert catalog.resolve("repo-a", rollback_view).commit_sha == "commit-1"


def test_late_build_cannot_overwrite_newer_repository_revision(tmp_path):
    manifests = {
        revision: _build(
            tmp_path,
            revision=revision,
            commit=commit,
            generation=generation,
            files=[
                SourceFile(
                    relative_path="service.py",
                    uri="viking://resources/repo-a/service.py",
                    content=f"def {symbol}():\n    return 1\n",
                )
            ],
        )
        for revision, commit, generation, symbol in (
            ("revision-1", "commit-1", "graph-1", "base_api"),
            ("revision-2", "commit-2", "graph-2", "late_api"),
            ("revision-3", "commit-3", "graph-3", "new_api"),
        )
    }
    catalog = LocalCodeGraphCatalog(tmp_path / "catalog.sqlite3", account_id=ACCOUNT_ID)

    catalog.publish(manifests["revision-1"], expected_epoch=0, base_revision_id=None)
    catalog.publish(
        manifests["revision-3"],
        expected_epoch=1,
        base_revision_id="revision-1",
    )

    with pytest.raises(SupersededRevisionError, match="actual 'revision-3'"):
        catalog.publish(
            manifests["revision-2"],
            expected_epoch=2,
            base_revision_id="revision-1",
        )

    assert catalog.resolve("repo-a", catalog.acquire_view()).revision_id == "revision-3"


def test_sealed_generation_cannot_be_overwritten(tmp_path):
    files = [
        SourceFile(
            relative_path="service.py",
            uri="viking://resources/repo-a/service.py",
            content="def run():\n    return 1\n",
        )
    ]
    _build(
        tmp_path,
        revision="revision-1",
        commit="commit-1",
        generation="graph-1",
        files=files,
    )

    with pytest.raises(FileExistsError, match="already exists"):
        _build(
            tmp_path,
            revision="revision-1",
            commit="commit-1",
            generation="graph-1",
            files=files,
        )


def test_catalog_cas_serializes_concurrent_repository_publications(tmp_path):
    manifest_a = _build(
        tmp_path,
        revision="revision-a",
        commit="commit-a",
        generation="graph-a",
        repo_id="repo-a",
        files=[
            SourceFile(
                relative_path="a.py",
                uri="viking://resources/repo-a/a.py",
                content="def from_a():\n    return 1\n",
            )
        ],
    )
    manifest_b = _build(
        tmp_path,
        revision="revision-b",
        commit="commit-b",
        generation="graph-b",
        repo_id="repo-b",
        files=[
            SourceFile(
                relative_path="b.py",
                uri="viking://resources/repo-b/b.py",
                content="def from_b():\n    return 2\n",
            )
        ],
    )
    catalog = LocalCodeGraphCatalog(tmp_path / "catalog.sqlite3", account_id=ACCOUNT_ID)

    def publish(manifest):
        try:
            return catalog.publish(manifest, expected_epoch=0, base_revision_id=None)
        except CatalogConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, (manifest_a, manifest_b)))

    successes = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, CatalogConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert catalog.current_epoch() == 1

    winner_repo = successes[0].repo_id
    loser = manifest_b if winner_repo == "repo-a" else manifest_a
    catalog.publish(loser, expected_epoch=1, base_revision_id=None)
    view = catalog.acquire_view()

    assert view.epoch == 2
    assert catalog.resolve("repo-a", view).revision_id == "revision-a"
    assert catalog.resolve("repo-b", view).revision_id == "revision-b"
