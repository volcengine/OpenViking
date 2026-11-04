"""Baseline and publication recovery decisions, independent of storage."""

import pytest
from pydantic import ValidationError

from openviking.service.artifact_relations_models import (
    ArtifactRelations,
    ArtifactRelationsState,
    RelationEdge,
    RelationSource,
    RelationSources,
    can_recover_publication,
    can_use_incremental_sources,
)


def graph(build_id="published", snapshot="snapshot-a"):
    return ArtifactRelations(
        build_id=build_id,
        target_snapshot_id=snapshot,
        generated_at=0,
        nodes=["viking://resources/wiki/"],
        edges=[],
    )


@pytest.mark.parametrize("source_build", ["candidate", None])
def test_unpublished_sources_cannot_be_used_after_content_is_restored(source_build):
    """A->B source overwrite, failed graph write, then B->A cannot hide wrong links."""
    sources = RelationSources(
        parser_version="markdown-v1",
        build_id=source_build,
        pages={"viking://resources/wiki/a.md": RelationSource(blob_oid="blob-b", links=["b.md"])},
    )
    assert not can_use_incremental_sources(sources, graph(), parser_version="markdown-v1")


def test_published_sources_require_the_same_parser_version():
    sources = RelationSources(parser_version="markdown-v1", build_id="published")
    assert can_use_incremental_sources(sources, graph(), parser_version="markdown-v1")
    assert not can_use_incremental_sources(sources, graph(), parser_version="markdown-v2")
    assert not can_use_incremental_sources(None, graph(), parser_version="markdown-v1")
    assert not can_use_incremental_sources(sources, None, parser_version="markdown-v1")


@pytest.mark.parametrize(
    "build_id,snapshot,phase,status,expected",
    [
        ("candidate", "snapshot-b", "publishing", "building", True),
        ("published", "snapshot-b", "publishing", "building", False),
        ("candidate", "snapshot-a", "publishing", "building", False),
        ("candidate", "snapshot-b", "computing", "building", False),
        ("candidate", "snapshot-b", "publishing", "failed", False),
        (None, "snapshot-b", "publishing", "building", False),
    ],
)
def test_only_the_current_publication_can_repair_ready(build_id, snapshot, phase, status, expected):
    state = ArtifactRelationsState(
        status=status,
        target_snapshot_id=snapshot,
        build_id=build_id,
        phase=phase,
    )
    assert can_recover_publication(state, graph("candidate", "snapshot-b")) is expected
    assert not can_recover_publication(state, None)


def test_missing_page_and_parsed_page_without_links_are_distinct():
    sources = RelationSources(
        parser_version="markdown-v1",
        pages={"a.md": RelationSource(blob_oid="blob-a", links=[])},
    )
    assert sources.pages["a.md"].links == []
    assert "b.md" not in sources.pages


def test_edges_preserve_the_persisted_from_field():
    edge = RelationEdge.model_validate(
        {
            "from": "viking://resources/wiki/a.md",
            "to": "viking://resources/wiki/b.md",
            "type": "references",
        }
    )
    assert edge.model_dump(by_alias=True)["from"] == edge.from_uri
    with pytest.raises(ValidationError):
        RelationEdge.model_validate({"from": "a.md", "to": "b.md", "type": "similarity"})
