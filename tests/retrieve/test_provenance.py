# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
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
