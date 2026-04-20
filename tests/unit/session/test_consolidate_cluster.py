# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MemoryDeduplicator.consolidate_cluster()."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.session.memory_deduplicator import (
    ClusterDecision,
    ClusterDecisionType,
    MemoryDeduplicator,
)
from tests.unit.conftest import make_test_context


def _ctx(uri: str, abstract: str = "abstract", active: int = 1):
    return make_test_context(uri, abstract=abstract, active_count=active)


def _make_dedup() -> MemoryDeduplicator:
    """Construct a MemoryDeduplicator without touching real config."""
    dedup = MemoryDeduplicator.__new__(MemoryDeduplicator)
    dedup.vikingdb = MagicMock()
    dedup.embedder = None
    return dedup


class TestConsolidateClusterEdgeCases:
    @pytest.mark.asyncio
    async def test_single_member_cluster_is_noop(self):
        dedup = _make_dedup()
        cluster = [_ctx("viking://agent/a/memories/patterns/x")]
        result = await dedup.consolidate_cluster(cluster, scope_uri="viking://agent/a/memories/patterns/")
        assert result.decision == ClusterDecisionType.KEEP_ALL
        assert "fewer than 2" in result.reason

    @pytest.mark.asyncio
    async def test_empty_cluster_is_noop(self):
        dedup = _make_dedup()
        result = await dedup.consolidate_cluster([], scope_uri="viking://agent/a/memories/patterns/")
        assert result.decision == ClusterDecisionType.KEEP_ALL

    @pytest.mark.asyncio
    async def test_no_llm_returns_keep_all(self):
        dedup = _make_dedup()
        cluster = [
            _ctx("viking://agent/a/memories/patterns/x"),
            _ctx("viking://agent/a/memories/patterns/y"),
        ]
        config_mock = MagicMock()
        config_mock.vlm = None
        with patch(
            "openviking.session.memory_deduplicator.get_openviking_config",
            return_value=config_mock,
        ):
            result = await dedup.consolidate_cluster(cluster, scope_uri="viking://agent/a/memories/patterns/")
        assert result.decision == ClusterDecisionType.KEEP_ALL
        assert "LLM not available" in result.reason


class TestParseClusterDecision:
    def test_keep_and_merge_normalized(self):
        cluster = [
            _ctx("viking://agent/a/memories/patterns/keeper"),
            _ctx("viking://agent/a/memories/patterns/dup"),
        ]
        payload = {
            "decision": "keep_and_merge",
            "reason": "Same fact",
            "keeper_uri": "viking://agent/a/memories/patterns/keeper",
            "merge_into": ["viking://agent/a/memories/patterns/dup"],
            "delete": [],
            "archive": [],
            "merged_content": "merged body",
            "merged_abstract": "merged abstract",
        }
        result = MemoryDeduplicator._parse_cluster_decision(payload, cluster)
        assert result.decision == ClusterDecisionType.KEEP_AND_MERGE
        assert result.keeper_uri == "viking://agent/a/memories/patterns/keeper"
        assert result.merge_into == ["viking://agent/a/memories/patterns/dup"]
        assert result.merged_content == "merged body"

    def test_keep_and_delete_strips_keeper_from_delete(self):
        cluster = [
            _ctx("viking://agent/a/memories/patterns/keeper"),
            _ctx("viking://agent/a/memories/patterns/stale"),
        ]
        payload = {
            "decision": "keep_and_delete",
            "keeper_uri": "viking://agent/a/memories/patterns/keeper",
            "delete": [
                "viking://agent/a/memories/patterns/keeper",
                "viking://agent/a/memories/patterns/stale",
            ],
        }
        result = MemoryDeduplicator._parse_cluster_decision(payload, cluster)
        assert result.decision == ClusterDecisionType.KEEP_AND_DELETE
        assert result.keeper_uri == "viking://agent/a/memories/patterns/keeper"
        assert result.delete == ["viking://agent/a/memories/patterns/stale"]

    def test_archive_all_archives_all_members_regardless_of_payload(self):
        cluster = [
            _ctx("viking://agent/a/memories/patterns/x"),
            _ctx("viking://agent/a/memories/patterns/y"),
        ]
        payload = {
            "decision": "archive_all",
            "keeper_uri": "viking://agent/a/memories/patterns/x",
            "archive": ["viking://agent/a/memories/patterns/x"],
        }
        result = MemoryDeduplicator._parse_cluster_decision(payload, cluster)
        assert result.decision == ClusterDecisionType.ARCHIVE_ALL
        assert result.keeper_uri == ""
        assert set(result.archive) == {
            "viking://agent/a/memories/patterns/x",
            "viking://agent/a/memories/patterns/y",
        }

    def test_keep_all_clears_all_action_lists(self):
        cluster = [
            _ctx("viking://agent/a/memories/patterns/x"),
            _ctx("viking://agent/a/memories/patterns/y"),
        ]
        payload = {
            "decision": "keep_all",
            "keeper_uri": "viking://agent/a/memories/patterns/x",
            "merge_into": ["viking://agent/a/memories/patterns/y"],
            "delete": ["viking://agent/a/memories/patterns/y"],
            "archive": ["viking://agent/a/memories/patterns/y"],
        }
        result = MemoryDeduplicator._parse_cluster_decision(payload, cluster)
        assert result.decision == ClusterDecisionType.KEEP_ALL
        assert result.keeper_uri == ""
        assert result.merge_into == []
        assert result.delete == []
        assert result.archive == []

    def test_unknown_decision_falls_back_to_keep_all(self):
        cluster = [
            _ctx("viking://agent/a/memories/patterns/x"),
            _ctx("viking://agent/a/memories/patterns/y"),
        ]
        payload = {"decision": "obliterate", "keeper_uri": ""}
        result = MemoryDeduplicator._parse_cluster_decision(payload, cluster)
        assert result.decision == ClusterDecisionType.KEEP_ALL

    def test_invalid_keeper_uri_falls_back_to_first_member(self):
        cluster = [
            _ctx("viking://agent/a/memories/patterns/first"),
            _ctx("viking://agent/a/memories/patterns/second"),
        ]
        payload = {
            "decision": "keep_and_merge",
            "keeper_uri": "viking://agent/a/memories/patterns/not-in-cluster",
            "merge_into": ["viking://agent/a/memories/patterns/second"],
        }
        result = MemoryDeduplicator._parse_cluster_decision(payload, cluster)
        assert result.keeper_uri == "viking://agent/a/memories/patterns/first"

    def test_action_uris_outside_cluster_are_dropped(self):
        cluster = [
            _ctx("viking://agent/a/memories/patterns/keeper"),
            _ctx("viking://agent/a/memories/patterns/dup"),
        ]
        payload = {
            "decision": "keep_and_merge",
            "keeper_uri": "viking://agent/a/memories/patterns/keeper",
            "merge_into": [
                "viking://agent/a/memories/patterns/dup",
                "viking://agent/a/memories/patterns/foreign",
            ],
        }
        result = MemoryDeduplicator._parse_cluster_decision(payload, cluster)
        assert result.merge_into == ["viking://agent/a/memories/patterns/dup"]


class TestConsolidateClusterLLMCall:
    @pytest.mark.asyncio
    async def test_keep_and_merge_happy_path(self):
        dedup = _make_dedup()
        cluster = [
            _ctx("viking://agent/a/memories/patterns/keeper", abstract="Use bun build"),
            _ctx("viking://agent/a/memories/patterns/dup", abstract="bun build for TS errors"),
        ]
        contents = {
            "viking://agent/a/memories/patterns/keeper": "Use `bun run build` to find TS errors.",
            "viking://agent/a/memories/patterns/dup": "Run `bun run build` to surface TS errors.",
        }
        vlm_mock = MagicMock()
        vlm_mock.is_available.return_value = True
        vlm_mock.get_completion_async = AsyncMock(
            return_value='{"decision":"keep_and_merge","keeper_uri":"viking://agent/a/memories/patterns/keeper","merge_into":["viking://agent/a/memories/patterns/dup"],"delete":[],"archive":[],"merged_content":"Use bun run build for TS errors.","merged_abstract":"bun build TS errors","reason":"same fact"}'
        )
        config_mock = MagicMock()
        config_mock.vlm = vlm_mock

        with patch(
            "openviking.session.memory_deduplicator.get_openviking_config",
            return_value=config_mock,
        ):
            result = await dedup.consolidate_cluster(
                cluster,
                scope_uri="viking://agent/a/memories/patterns/",
                cluster_contents=contents,
            )

        assert result.decision == ClusterDecisionType.KEEP_AND_MERGE
        assert result.keeper_uri == "viking://agent/a/memories/patterns/keeper"
        assert result.merge_into == ["viking://agent/a/memories/patterns/dup"]
        assert result.merged_content.startswith("Use bun run build")

    @pytest.mark.asyncio
    async def test_llm_failure_returns_keep_all(self):
        dedup = _make_dedup()
        cluster = [
            _ctx("viking://agent/a/memories/patterns/x"),
            _ctx("viking://agent/a/memories/patterns/y"),
        ]
        vlm_mock = MagicMock()
        vlm_mock.is_available.return_value = True
        vlm_mock.get_completion_async = AsyncMock(side_effect=RuntimeError("boom"))
        config_mock = MagicMock()
        config_mock.vlm = vlm_mock

        with patch(
            "openviking.session.memory_deduplicator.get_openviking_config",
            return_value=config_mock,
        ):
            result = await dedup.consolidate_cluster(
                cluster, scope_uri="viking://agent/a/memories/patterns/"
            )

        assert result.decision == ClusterDecisionType.KEEP_ALL
        assert "LLM failed" in result.reason
