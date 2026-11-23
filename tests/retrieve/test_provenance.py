# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public search result serialization."""

from openviking_cli.retrieve.types import (
    ContextType,
    FindResult,
    MatchedContext,
    QueryResult,
    TypedQuery,
)


def test_to_dict_with_provenance_preserves_the_regular_result():
    uri = "viking://resources/docs/arch.md"
    hit = MatchedContext(
        uri=uri,
        context_type=ContextType.RESOURCE,
        level=2,
        abstract="Architecture doc",
        score=0.87,
        match_reason="semantic_match",
    )
    query = TypedQuery(query="architecture", context_type=ContextType.RESOURCE, intent="")
    directories = ["viking://resources/docs"]
    result = FindResult(
        memories=[],
        resources=[hit],
        skills=[],
        query_results=[
            QueryResult(query=query, matched_contexts=[hit], searched_directories=directories)
        ],
    )

    plain = result.to_dict()
    assert plain == result.to_dict(include_provenance=False)
    assert "provenance" not in plain
    assert plain["total"] == 1

    with_provenance = result.to_dict(include_provenance=True)
    [provenance] = with_provenance.pop("provenance")
    assert with_provenance == plain
    assert provenance["query"] == "architecture"
    assert provenance["searched_directories"] == directories
    assert provenance["matched_contexts"] == [
        {
            "uri": uri,
            "tier": "L2",
            "context_type": "resource",
            "score": 0.87,
            "match_reason": "semantic_match",
        }
    ]
    assert "statistics" in provenance["thinking_trace"]


def test_provenance_without_query_results():
    result = FindResult(memories=[], resources=[], skills=[])
    assert "provenance" not in result.to_dict(include_provenance=True)


def test_result_serializes_public_fields_and_round_trips_tags():
    uri = "viking://resources/docs/arch.md"
    hit = MatchedContext(
        uri=uri,
        context_type=ContextType.RESOURCE,
        level=2,
        abstract="Architecture doc",
        score=0.5,
        search_tags=["default", "team=infra", "bad=", "project=viking"],
    )
    untagged = MatchedContext(
        uri="viking://resources/docs/readme.md", context_type=ContextType.RESOURCE
    )
    result = FindResult(memories=[], resources=[hit, untagged], skills=[]).to_dict()

    assert result["resources"][0] == {
        "uri": uri,
        "context_type": "resource",
        "level": 2,
        "abstract": "Architecture doc",
        "score": 0.5,
        "tags": ["team=infra", "project=viking"],
    }
    assert result["resources"][1]["tags"] == []
    restored = FindResult.from_dict(result)
    assert [item.search_tags for item in restored.resources] == [
        ["team=infra", "project=viking"],
        [],
    ]
