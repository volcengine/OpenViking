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

    def test_to_dict_without_provenance(self):
        result = self._make_find_result()
        d = result.to_dict(include_provenance=False)
        assert "provenance" not in d
        assert d["total"] == 1
        assert len(d["resources"]) == 1

    def test_to_dict_with_provenance(self):
        result = self._make_find_result()
        d = result.to_dict(include_provenance=True)
        assert "provenance" in d
        assert len(d["provenance"]) == 1

        prov = d["provenance"][0]
        assert prov["query"] == "architecture"
        assert prov["searched_directories"] == ["resources/", "resources/docs/"]
        assert len(prov["matched_contexts"]) == 1

        ctx = prov["matched_contexts"][0]
        assert ctx["uri"] == "viking://resources/docs/arch.md"
        assert ctx["tier"] == "L2"
        assert ctx["context_type"] == "resource"
        assert ctx["score"] == 0.87
        assert ctx["match_reason"] == "semantic_match"

        assert "thinking_trace" in prov
        assert "statistics" in prov["thinking_trace"]

    def test_to_dict_default_no_provenance(self):
        result = self._make_find_result()
        d = result.to_dict()
        assert "provenance" not in d

    def test_provenance_without_query_results(self):
        result = FindResult(memories=[], resources=[], skills=[])
        d = result.to_dict(include_provenance=True)
        assert "provenance" not in d

    def test_existing_fields_unchanged_with_provenance(self):
        result = self._make_find_result()
        d_without = result.to_dict(include_provenance=False)
        d_with = result.to_dict(include_provenance=True)

        # All existing fields should be identical
        assert d_without["memories"] == d_with["memories"]
        assert d_without["resources"] == d_with["resources"]
        assert d_without["skills"] == d_with["skills"]
        assert d_without["total"] == d_with["total"]


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

    def test_context_to_dict_defaults_empty_tags(self):
        ctx = MatchedContext(
            uri="viking://resources/docs/arch.md",
            context_type=ContextType.RESOURCE,
            level=2,
        )
        result = FindResult(memories=[], resources=[ctx], skills=[])
        d = result.to_dict()
        assert d["resources"][0]["tags"] == []

    def test_context_to_dict_drops_always_empty_fields(self):
        ctx = MatchedContext(
            uri="viking://resources/docs/arch.md",
            context_type=ContextType.RESOURCE,
            level=2,
        )
        result = FindResult(memories=[], resources=[ctx], skills=[])
        item = result.to_dict()["resources"][0]
        for dropped in ("category", "match_reason", "overview"):
            assert dropped not in item

    def test_context_to_dict_keeps_useful_fields(self):
        ctx = MatchedContext(
            uri="viking://resources/docs/arch.md",
            context_type=ContextType.RESOURCE,
            level=2,
            abstract="Architecture doc",
            score=0.5,
        )
        result = FindResult(memories=[], resources=[ctx], skills=[])
        item = result.to_dict()["resources"][0]
        assert item["uri"] == "viking://resources/docs/arch.md"
        assert item["context_type"] == "resource"
        assert item["level"] == 2
        assert item["score"] == 0.5
        assert item["abstract"] == "Architecture doc"

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
