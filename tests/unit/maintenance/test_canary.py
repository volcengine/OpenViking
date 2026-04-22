# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the canary phase of MemoryConsolidator (Phase D)."""

from unittest.mock import MagicMock, patch

import pytest

from openviking.maintenance import Canary, CanaryResult
from openviking.maintenance.memory_consolidator import MemoryConsolidator
from tests.unit.maintenance.conftest import (
    make_consolidator as _make_consolidator,
    make_request_ctx as _make_request_ctx,
    noop_lock as _noop_lock,
)


class TestCanaryStructure:
    def test_canary_from_dict(self):
        c = Canary.from_dict({"query": "how do I X", "expected_top_uri": "viking://x"})
        assert c.query == "how do I X"
        assert c.expected_top_uri == "viking://x"
        assert c.top_n == 5

    def test_canary_from_dict_handles_missing_keys(self):
        c = Canary.from_dict({})
        assert c.query == ""
        assert c.expected_top_uri == ""
        assert c.top_n == 5

    def test_canary_from_dict_respects_explicit_top_n(self):
        c = Canary.from_dict(
            {"query": "q", "expected_top_uri": "viking://x", "top_n": 1}
        )
        assert c.top_n == 1

    def test_canary_from_dict_clamps_bad_top_n_to_default(self):
        c = Canary.from_dict(
            {"query": "q", "expected_top_uri": "viking://x", "top_n": "garbage"}
        )
        assert c.top_n == 5

    def test_canary_from_dict_clamps_non_positive_top_n(self):
        c = Canary.from_dict(
            {"query": "q", "expected_top_uri": "viking://x", "top_n": 0}
        )
        assert c.top_n == 1


class TestRunCanaries:
    @pytest.mark.asyncio
    async def test_canary_satisfied_when_expected_uri_in_top(self):
        consolidator = _make_consolidator(
            search_results={
                "memories": [
                    {"uri": "viking://x/memories/patterns/keeper.md"},
                    {"uri": "viking://x/memories/patterns/other.md"},
                ]
            }
        )
        canaries = [
            Canary(
                query="how do I build",
                expected_top_uri="viking://x/memories/patterns/keeper.md",
            )
        ]
        results = await consolidator._run_canaries(
            "viking://x/memories/patterns/", canaries, _make_request_ctx()
        )
        assert len(results) == 1
        r = results[0]
        assert r["found_in_top_n"] is True
        assert r["found_position"] == 0
        assert r["found_top_uri"] == "viking://x/memories/patterns/keeper.md"

    @pytest.mark.asyncio
    async def test_canary_unsatisfied_when_expected_missing(self):
        consolidator = _make_consolidator(
            search_results={"memories": [{"uri": "viking://x/memories/patterns/other.md"}]}
        )
        canaries = [
            Canary(
                query="how do I build",
                expected_top_uri="viking://x/memories/patterns/keeper.md",
            )
        ]
        results = await consolidator._run_canaries(
            "viking://x/memories/patterns/", canaries, _make_request_ctx()
        )
        assert results[0]["found_in_top_n"] is False
        assert results[0]["found_position"] == -1

    @pytest.mark.asyncio
    async def test_canary_swallows_search_failure(self):
        consolidator = _make_consolidator(
            search_results=lambda **_: (_ for _ in ()).throw(RuntimeError("search down"))
        )
        canaries = [Canary(query="x", expected_top_uri="viking://y")]
        results = await consolidator._run_canaries(
            "viking://x/", canaries, _make_request_ctx()
        )
        assert results[0]["found_in_top_n"] is False

    @pytest.mark.asyncio
    async def test_no_service_returns_empty_uris(self):
        consolidator = _make_consolidator(with_service=False)
        canaries = [Canary(query="x", expected_top_uri="viking://y")]
        results = await consolidator._run_canaries(
            "viking://x/", canaries, _make_request_ctx()
        )
        assert results[0]["found_in_top_n"] is False

    @pytest.mark.asyncio
    async def test_strict_canary_top_n_1_catches_position_demotion(self):
        # The whole point of per-canary top_n: a strict canary should
        # flag when the expected URI demotes from position 0 to 2,
        # even though a default top_n=5 would still consider it passing.
        consolidator = _make_consolidator(
            search_results={
                "memories": [
                    {"uri": "viking://x/other.md"},
                    {"uri": "viking://x/another.md"},
                    {"uri": "viking://x/expected.md"},
                ]
            }
        )
        canaries = [
            Canary(query="q", expected_top_uri="viking://x/expected.md", top_n=1)
        ]
        results = await consolidator._run_canaries(
            "viking://x/", canaries, _make_request_ctx()
        )
        consolidator.service.search.search.assert_awaited_once()
        call_kwargs = consolidator.service.search.search.call_args.kwargs
        assert call_kwargs["limit"] == 1
        assert results[0]["top_n"] == 1
        assert results[0]["found_in_top_n"] is False

    @pytest.mark.asyncio
    async def test_loose_canary_top_n_5_accepts_top_3_position(self):
        # Same underlying shape, but top_n=5 accepts position-2 as a pass.
        consolidator = _make_consolidator(
            search_results={
                "memories": [
                    {"uri": "viking://x/other.md"},
                    {"uri": "viking://x/another.md"},
                    {"uri": "viking://x/expected.md"},
                ]
            }
        )
        canaries = [
            Canary(query="q", expected_top_uri="viking://x/expected.md", top_n=5)
        ]
        results = await consolidator._run_canaries(
            "viking://x/", canaries, _make_request_ctx()
        )
        call_kwargs = consolidator.service.search.search.call_args.kwargs
        assert call_kwargs["limit"] == 5
        assert results[0]["found_in_top_n"] is True
        assert results[0]["found_position"] == 2


class TestMultiCanaryOutcomes:
    """Mixed-outcome semantics across multiple canaries in one run."""

    @pytest.mark.asyncio
    async def test_multi_canary_mixed_pass_and_fail_preserved(self):
        # Search returns different shapes per query (via callable side_effect).
        def search_by_query(**kwargs):
            query = kwargs.get("query", "")
            if query == "pass":
                return {"memories": [{"uri": "viking://x/pass.md"}]}
            if query == "fail":
                return {"memories": [{"uri": "viking://x/other.md"}]}
            return {"memories": []}

        consolidator = _make_consolidator(search_results=search_by_query)
        canaries = [
            Canary(query="pass", expected_top_uri="viking://x/pass.md"),
            Canary(query="fail", expected_top_uri="viking://x/missing.md"),
            Canary(query="unknown", expected_top_uri="viking://x/anything.md"),
        ]
        results = await consolidator._run_canaries(
            "viking://x/", canaries, _make_request_ctx()
        )

        assert len(results) == 3
        # Per-canary results preserved in insertion order.
        assert results[0]["query"] == "pass" and results[0]["found_in_top_n"] is True
        assert results[1]["query"] == "fail" and results[1]["found_in_top_n"] is False
        assert results[2]["query"] == "unknown" and results[2]["found_in_top_n"] is False

    @pytest.mark.asyncio
    async def test_any_regression_flags_overall_failed(self):
        # 2 canaries, 1 regresses. Whole run marked canary_failed=true.
        search_calls = [0]

        def search(**kwargs):
            query = kwargs.get("query", "")
            call_num = search_calls[0]
            search_calls[0] += 1
            # "a" passes both pre and post; "b" passes pre, fails post.
            if query == "a":
                return {"memories": [{"uri": "viking://x/a.md"}]}
            if query == "b":
                if call_num < 2:
                    return {"memories": [{"uri": "viking://x/b.md"}]}
                return {"memories": [{"uri": "viking://x/other.md"}]}
            return {"memories": []}

        consolidator = _make_consolidator(search_results=search)
        canaries = [
            Canary(query="a", expected_top_uri="viking://x/a.md"),
            Canary(query="b", expected_top_uri="viking://x/b.md"),
        ]
        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch(
                "openviking.maintenance.memory_consolidator.get_lock_manager",
                return_value=MagicMock(),
            ),
        ):
            result = await consolidator.run(
                "viking://x/",
                _make_request_ctx(),
                canaries=canaries,
            )
        assert result.canary_failed is True
        # The "a" canary was satisfied both pre and post; only "b" regressed.
        pre_b = next(r for r in result.canaries_pre if r["query"] == "b")
        post_b = next(r for r in result.canaries_post if r["query"] == "b")
        assert pre_b["found_in_top_n"] is True
        assert post_b["found_in_top_n"] is False


class TestEdgeCaseInputs:
    @pytest.mark.asyncio
    async def test_empty_query_does_not_crash(self):
        # Defensive: a canary with an empty query string shouldn't
        # explode the run. Result records the miss.
        consolidator = _make_consolidator(search_results={"memories": []})
        canaries = [Canary(query="", expected_top_uri="viking://x/y.md")]
        results = await consolidator._run_canaries(
            "viking://x/", canaries, _make_request_ctx()
        )
        assert len(results) == 1
        assert results[0]["found_in_top_n"] is False


class TestMergedIntoKeeperLimitation:
    """Documents a known false-regression case.

    When a canary's expected_top_uri was merged into a keeper (source URI
    deleted, content preserved in keeper), the canary fails post even
    though the user's query may still find the right content under a
    different URI. A future enhancement should cross-reference
    applied_uris + cluster_decisions to classify this as
    "migrated, not lost."
    """

    @pytest.mark.xfail(
        strict=True,
        reason="Known false-regression: merged-into-keeper currently flags canary_failed. "
        "When cross-reference logic lands, this test flips to passing and xfail should be removed.",
    )
    @pytest.mark.asyncio
    async def test_merged_source_should_not_flag_regression(self):
        # Pre search finds the source; post search finds the keeper
        # (because source was merged into it). The user's intent ("this
        # query should still find useful content") is satisfied.
        search_calls = [0]

        def search(**kwargs):
            call_num = search_calls[0]
            search_calls[0] += 1
            if call_num == 0:
                return {"memories": [{"uri": "viking://x/source.md"}]}
            return {"memories": [{"uri": "viking://x/keeper.md"}]}

        consolidator = _make_consolidator(search_results=search)
        canaries = [Canary(query="q", expected_top_uri="viking://x/source.md")]

        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch(
                "openviking.maintenance.memory_consolidator.get_lock_manager",
                return_value=MagicMock(),
            ),
        ):
            result = await consolidator.run(
                "viking://x/",
                _make_request_ctx(),
                canaries=canaries,
            )
        # Target behavior: canary should NOT fail when source migrated.
        assert result.canary_failed is False


class TestCanaryRegression:
    def test_no_regression_when_both_satisfied(self):
        pre = [{"query": "q", "found_in_top_n": True, "found_position": 0}]
        post = [{"query": "q", "found_in_top_n": True, "found_position": 1}]
        assert MemoryConsolidator._canary_regressed(pre, post) is False

    def test_regression_when_pre_passed_post_failed(self):
        pre = [{"query": "q", "found_in_top_n": True, "found_position": 0}]
        post = [{"query": "q", "found_in_top_n": False, "found_position": -1}]
        assert MemoryConsolidator._canary_regressed(pre, post) is True

    def test_no_regression_when_both_failed(self):
        # Pre-existing miss is not a regression.
        pre = [{"query": "q", "found_in_top_n": False, "found_position": -1}]
        post = [{"query": "q", "found_in_top_n": False, "found_position": -1}]
        assert MemoryConsolidator._canary_regressed(pre, post) is False

    def test_no_regression_for_post_only_canary(self):
        pre = []
        post = [{"query": "q", "found_in_top_n": False, "found_position": -1}]
        assert MemoryConsolidator._canary_regressed(pre, post) is False


class TestRunWithCanaries:
    @pytest.mark.asyncio
    async def test_canary_phases_recorded_on_run(self):
        consolidator = _make_consolidator(
            search_results={"memories": [{"uri": "viking://x/m/keeper.md"}]}
        )
        canaries = [Canary(query="x", expected_top_uri="viking://x/m/keeper.md")]
        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch(
                "openviking.maintenance.memory_consolidator.get_lock_manager",
                return_value=MagicMock(),
            ),
        ):
            result = await consolidator.run(
                "viking://x/m/",
                _make_request_ctx(),
                canaries=canaries,
            )
        assert len(result.canaries_pre) == 1
        assert len(result.canaries_post) == 1
        assert result.canary_failed is False

    @pytest.mark.asyncio
    async def test_dry_run_skips_canaries(self):
        consolidator = _make_consolidator()
        canaries = [Canary(query="x", expected_top_uri="viking://x")]
        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch(
                "openviking.maintenance.memory_consolidator.get_lock_manager",
                return_value=MagicMock(),
            ),
        ):
            result = await consolidator.run(
                "viking://x/m/",
                _make_request_ctx(),
                dry_run=True,
                canaries=canaries,
            )
        assert result.canaries_pre == []
        assert result.canaries_post == []
