# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for search result provenance metadata."""

from __future__ import annotations

from openviking_cli.retrieve.types import (
    ContextType,
    FindResult,
    MatchedContext,
    QueryResult,
    ThinkingTrace,
    TypedQuery,
)


class TestFindResultProvenance:
    def _make_find_result(self) -> FindResult:
        """Build a FindResult with query_results for testing."""
        ctx = MatchedContext(
            uri="viking://resources/docs/arch.md",
            context_type=ContextType.RESOURCE,
            level=2,
            abstract="Architecture doc",
            score=0.87,
            match_reason="semantic_match",
        )
        query = TypedQuery(
            query="architecture",
            context_type=ContextType.RESOURCE,
            intent="find architecture docs",
        )
        trace = ThinkingTrace()
        qr = QueryResult(
            query=query,
            matched_contexts=[ctx],
            searched_directories=["resources/", "resources/docs/"],
            thinking_trace=trace,
        )
        return FindResult(
            memories=[],
            resources=[ctx],
            skills=[],
            query_results=[qr],
        )

    def test_provenance_is_opt_in_and_preserves_the_hit_list(self):
        result = self._make_find_result()
        default = result.to_dict()
        assert "provenance" not in default
        assert default == result.to_dict(include_provenance=False)
        assert default["total"] == len(default["resources"]) == 1

        with_provenance = result.to_dict(include_provenance=True)
        (provenance,) = with_provenance.pop("provenance")
        assert with_provenance == default
        assert provenance["query"] == "architecture"
        assert provenance["searched_directories"] == ["resources/", "resources/docs/"]
        (hit,) = provenance["matched_contexts"]
        assert hit["uri"] == default["resources"][0]["uri"]
        assert hit["tier"] == "L2"
        assert hit["context_type"] == "resource"
        assert hit["score"] == default["resources"][0]["score"] == 0.87
        assert hit["match_reason"] == "semantic_match"
        assert "statistics" in provenance["thinking_trace"]

    def test_provenance_without_query_results(self):
        result = FindResult(memories=[], resources=[], skills=[])
        assert "provenance" not in result.to_dict(include_provenance=True)


class TestMatchedContextSearchTags:
    def test_context_to_dict_exposes_search_tags_as_tags(self):
        ctx = MatchedContext(
            uri="viking://resources/docs/arch.md",
            context_type=ContextType.RESOURCE,
            level=2,
            search_tags=["team=infra", "project=viking"],
        )
        result = FindResult(memories=[], resources=[ctx], skills=[])
        d = result.to_dict()
        assert d["resources"][0]["tags"] == ["team=infra", "project=viking"]
        assert "search_tags" not in d["resources"][0]

    def test_context_to_dict_discards_legacy_invalid_tags(self):
        ctx = MatchedContext(
            uri="viking://resources/docs/arch.md",
            context_type=ContextType.RESOURCE,
            level=2,
            search_tags=["default", "team=infra", "bad=", "project=viking"],
        )

        result = FindResult(memories=[], resources=[ctx], skills=[])

        assert result.to_dict()["resources"][0]["tags"] == [
            "team=infra",
            "project=viking",
        ]

    def test_context_serializes_the_hit_and_omits_internal_fields(self):
        ctx = MatchedContext(
            uri="viking://resources/docs/arch.md",
            context_type=ContextType.RESOURCE,
            level=2,
            abstract="Architecture doc",
            score=0.5,
        )
        result = FindResult(memories=[], resources=[ctx], skills=[])
        assert result.to_dict()["resources"] == [
            {
                "uri": "viking://resources/docs/arch.md",
                "context_type": "resource",
                "level": 2,
                "score": 0.5,
                "abstract": "Architecture doc",
                "tags": [],
            }
        ]

    def test_from_dict_round_trips_tags(self):
        payload = {
            "resources": [
                {
                    "uri": "viking://resources/docs/arch.md",
                    "context_type": "resource",
                    "level": 2,
                    "tags": ["team=infra"],
                }
            ]
        }
        result = FindResult.from_dict(payload)
        assert result.resources[0].search_tags == ["team=infra"]
