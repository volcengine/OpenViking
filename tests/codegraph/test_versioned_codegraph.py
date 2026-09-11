# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openviking.codegraph import (
    CatalogConflictError,
    CodeGraphBuilder,
    CodeGraphIndex,
    LocalCodeGraphCatalog,
    SourceFile,
    VersionedCodeGraph,
)


def _build(
    root: Path,
    *,
    revision: str,
    commit: str,
    generation: str,
    files: list[SourceFile],
    repo_id: str = "repo-a",
):
    return CodeGraphBuilder().build(
        repo_id=repo_id,
        revision_id=revision,
        commit_sha=commit,
        source_snapshot_oid=f"snapshot-{commit}",
        graph_generation=generation,
        source_files=files,
        output_path=root / f"{generation}.sqlite3",
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
    hits = index.search("greet")

    assert hits
    method = next(hit for hit in hits if hit.qualified_name == "Greeter.greet")
    assert method.kind == "method"
    assert method.canonical_signature == "(p:str)->str"
    assert method.start_line == 2

    expansion = index.expand([method.node_id])

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
        for hit in CodeGraphIndex(manifest.index_path).search("parse", limit=10)
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
        for hit in CodeGraphIndex(manifest.index_path).search("cleanup", limit=10)
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

    assert index.search("private_secret")
    authorized_hits = index.search("private_secret", allowed_file_ids={1})
    assert authorized_hits
    assert {hit.file_id for hit in authorized_hits} == {1}
    assert "private_secret" not in {hit.qualified_name for hit in authorized_hits}

    unfiltered_public = next(
        hit for hit in index.search("public_entry") if hit.qualified_name == "public_entry"
    )
    unfiltered_expansion = index.expand([unfiltered_public.node_id])
    assert len(unfiltered_expansion.edges) == 1
    assert unfiltered_expansion.edges[0].resolution == "best_effort"

    public = next(
        hit
        for hit in index.search("public_entry", allowed_file_ids={1})
        if hit.qualified_name == "public_entry"
    )
    expansion = index.expand([public.node_id], allowed_file_ids={1})

    assert {node.qualified_name for node in expansion.nodes} == {"public_entry"}
    assert expansion.edges == ()


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
    catalog = LocalCodeGraphCatalog(tmp_path / "catalog.sqlite3", account_id="account-1")
    service = VersionedCodeGraph(catalog)

    published1 = catalog.publish(manifest1, expected_epoch=0)
    view1 = service.acquire_view()

    assert published1.published_epoch == 1
    assert [hit.qualified_name for hit in service.search("repo-a", "old_api")[2]] == ["old_api"]

    # Building graph-2 alone does not change the published catalog.
    assert service.search("repo-a", "new_api")[2] == []

    published2 = catalog.publish(manifest2, expected_epoch=1)
    latest_view, latest_revision, latest_hits = service.search("repo-a", "new_api")
    old_view, old_revision, old_hits = service.search(
        "repo-a",
        "old_api",
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
        catalog.publish(manifest1, expected_epoch=1)

    rollback = catalog.publish(manifest1, expected_epoch=2)
    rollback_view = catalog.acquire_view()

    assert rollback.published_epoch == 3
    assert catalog.resolve("repo-a", latest_view).commit_sha == "commit-2"
    assert catalog.resolve("repo-a", rollback_view).commit_sha == "commit-1"


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
    catalog = LocalCodeGraphCatalog(tmp_path / "catalog.sqlite3", account_id="account-1")

    def publish(manifest):
        try:
            return catalog.publish(manifest, expected_epoch=0)
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
    catalog.publish(loser, expected_epoch=1)
    view = catalog.acquire_view()

    assert view.epoch == 2
    assert catalog.resolve("repo-a", view).revision_id == "revision-a"
    assert catalog.resolve("repo-b", view).revision_id == "revision-b"
