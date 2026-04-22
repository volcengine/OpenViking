# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MemoryConsolidator orchestrator."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.maintenance.memory_consolidator import (
    AUDIT_PATH_FRAGMENT,
    ConsolidationResult,
    MemoryConsolidator,
)
from openviking.session.memory_deduplicator import (
    ClusterDecision,
    ClusterDecisionType,
)
from tests.unit.conftest import make_test_context as _ctx
from tests.unit.maintenance.conftest import (
    make_consolidator,
    make_request_ctx,
    noop_lock,
)


# Local aliases keep the existing test bodies untouched.
def _make_consolidator(**kwargs):
    return make_consolidator(with_service=False, **kwargs)


_make_request_ctx = make_request_ctx
_noop_lock = noop_lock


class TestRunHappyPath:
    @pytest.mark.asyncio
    async def test_dry_run_writes_no_files_and_records_plan(self):
        archive = [MagicMock()]
        consolidator = _make_consolidator(archive_candidates=archive)
        consolidator._cluster_scope = AsyncMock(
            return_value=[
                [
                    _ctx("viking://agent/a/memories/patterns/x"),
                    _ctx("viking://agent/a/memories/patterns/y"),
                ]
            ]
        )

        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch("openviking.maintenance.memory_consolidator.get_lock_manager", return_value=MagicMock()),
        ):
            result = await consolidator.run(
                "viking://agent/a/memories/patterns/",
                _make_request_ctx(),
                dry_run=True,
            )

        assert result.dry_run is True
        assert result.candidates["merge_clusters"] == 1
        assert result.candidates["archive"] == 1
        consolidator.dedup.consolidate_cluster.assert_not_called()
        consolidator.archiver.archive.assert_not_called()
        consolidator.viking_fs.write.assert_called_once()  # audit only

    @pytest.mark.asyncio
    async def test_keep_and_merge_writes_keeper_and_deletes_sources(self):
        cluster = [
            _ctx("viking://agent/a/memories/patterns/keeper"),
            _ctx("viking://agent/a/memories/patterns/dup"),
        ]
        decision = ClusterDecision(
            decision=ClusterDecisionType.KEEP_AND_MERGE,
            cluster=cluster,
            keeper_uri="viking://agent/a/memories/patterns/keeper",
            merge_into=["viking://agent/a/memories/patterns/dup"],
            merged_content="merged body",
            merged_abstract="merged abstract",
            reason="same fact",
        )
        consolidator = _make_consolidator(cluster_decision=decision)
        consolidator._cluster_scope = AsyncMock(return_value=[cluster])

        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch("openviking.maintenance.memory_consolidator.get_lock_manager", return_value=MagicMock()),
        ):
            result = await consolidator.run(
                "viking://agent/a/memories/patterns/",
                _make_request_ctx(),
            )

        # keeper write + audit = 2
        assert consolidator.viking_fs.write.call_count == 2
        consolidator.viking_fs.rm.assert_called_once()
        rm_call = consolidator.viking_fs.rm.call_args
        assert rm_call.args[0] == "viking://agent/a/memories/patterns/dup"
        assert "ctx" in rm_call.kwargs
        assert "lock_handle" in rm_call.kwargs
        assert result.ops_applied["merged"] == 1
        assert "viking://agent/a/memories/patterns/keeper" in result.applied_uris
        assert "viking://agent/a/memories/patterns/dup" in result.applied_uris

    @pytest.mark.asyncio
    async def test_keep_and_merge_with_empty_content_skips_deletes(self):
        # Regression: empty merged_content used to delete sources without
        # writing keeper -> data loss. Now skipped, marked partial.
        cluster = [
            _ctx("viking://agent/a/memories/patterns/keeper"),
            _ctx("viking://agent/a/memories/patterns/dup"),
        ]
        decision = ClusterDecision(
            decision=ClusterDecisionType.KEEP_AND_MERGE,
            cluster=cluster,
            keeper_uri="viking://agent/a/memories/patterns/keeper",
            merge_into=["viking://agent/a/memories/patterns/dup"],
            merged_content="",  # bug trigger
            merged_abstract="",
        )
        consolidator = _make_consolidator(cluster_decision=decision)
        consolidator._cluster_scope = AsyncMock(return_value=[cluster])

        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch("openviking.maintenance.memory_consolidator.get_lock_manager", return_value=MagicMock()),
        ):
            result = await consolidator.run(
                "viking://agent/a/memories/patterns/",
                _make_request_ctx(),
            )

        consolidator.viking_fs.rm.assert_not_called()
        assert result.ops_applied["merged"] == 0
        assert result.partial is True
        assert any("merge_skipped_empty_content" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_keep_and_delete_drops_invalidated_members(self):
        cluster = [
            _ctx("viking://agent/a/memories/preferences/k"),
            _ctx("viking://agent/a/memories/preferences/old"),
        ]
        decision = ClusterDecision(
            decision=ClusterDecisionType.KEEP_AND_DELETE,
            cluster=cluster,
            keeper_uri="viking://agent/a/memories/preferences/k",
            delete=["viking://agent/a/memories/preferences/old"],
            reason="user changed editors",
        )
        consolidator = _make_consolidator(cluster_decision=decision)
        consolidator._cluster_scope = AsyncMock(return_value=[cluster])

        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch("openviking.maintenance.memory_consolidator.get_lock_manager", return_value=MagicMock()),
        ):
            result = await consolidator.run(
                "viking://agent/a/memories/preferences/",
                _make_request_ctx(),
            )

        consolidator.viking_fs.rm.assert_called_once()
        assert result.ops_applied["deleted"] == 1
        assert result.ops_applied["merged"] == 0


class TestEmptyScope:
    @pytest.mark.asyncio
    async def test_empty_scope_is_clean_noop(self):
        consolidator = _make_consolidator()  # no clusters, no archive

        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch("openviking.maintenance.memory_consolidator.get_lock_manager", return_value=MagicMock()),
        ):
            result = await consolidator.run(
                "viking://agent/a/memories/patterns/",
                _make_request_ctx(),
            )

        assert result.candidates["merge_clusters"] == 0
        assert result.candidates["archive"] == 0
        assert result.ops_applied["merged"] == 0
        assert result.ops_applied["deleted"] == 0
        assert result.ops_applied["archived"] == 0
        assert not result.partial
        # Audit still written.
        consolidator.viking_fs.write.assert_called_once()


class TestPartialFailure:
    @pytest.mark.asyncio
    async def test_one_cluster_fails_others_commit(self):
        good_cluster = [
            _ctx("viking://agent/a/memories/patterns/g1"),
            _ctx("viking://agent/a/memories/patterns/g2"),
        ]
        bad_cluster = [
            _ctx("viking://agent/a/memories/patterns/b1"),
            _ctx("viking://agent/a/memories/patterns/b2"),
        ]
        consolidator = _make_consolidator()
        consolidator._cluster_scope = AsyncMock(return_value=[good_cluster, bad_cluster])

        good_decision = ClusterDecision(
            decision=ClusterDecisionType.KEEP_AND_DELETE,
            cluster=good_cluster,
            keeper_uri="viking://agent/a/memories/patterns/g1",
            delete=["viking://agent/a/memories/patterns/g2"],
        )

        async def consolidate_side_effect(cluster, **kwargs):
            if cluster is bad_cluster:
                raise RuntimeError("bad cluster boom")
            return good_decision

        consolidator.dedup.consolidate_cluster = AsyncMock(side_effect=consolidate_side_effect)

        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch("openviking.maintenance.memory_consolidator.get_lock_manager", return_value=MagicMock()),
        ):
            result = await consolidator.run(
                "viking://agent/a/memories/patterns/",
                _make_request_ctx(),
            )

        assert result.partial is True
        assert any("cluster_failed" in e for e in result.errors)
        # Good cluster's delete still applied.
        assert result.ops_applied["deleted"] == 1


class TestAuditRecord:
    @pytest.mark.asyncio
    async def test_audit_uri_is_account_scoped_and_payload_is_valid_json(self):
        consolidator = _make_consolidator()

        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch("openviking.maintenance.memory_consolidator.get_lock_manager", return_value=MagicMock()),
        ):
            result = await consolidator.run(
                "viking://agent/test-account/memories/patterns/",
                _make_request_ctx("test-account"),
            )

        assert result.audit_uri.startswith(
            f"viking://agent/test-account/{AUDIT_PATH_FRAGMENT}/"
        )
        assert result.audit_uri.endswith(".json")
        # Last write call is the audit; payload must be valid JSON.
        write_call = consolidator.viking_fs.write.call_args_list[-1]
        payload = write_call.args[1]
        parsed = json.loads(payload)
        assert parsed["scope_uri"] == "viking://agent/test-account/memories/patterns/"
        assert "phase_durations" in parsed
        assert "ops_applied" in parsed

    @pytest.mark.asyncio
    async def test_default_account_when_ctx_missing_account_id(self):
        consolidator = _make_consolidator()
        ctx = MagicMock(spec=[])  # no account_id attribute

        with (
            patch("openviking.maintenance.memory_consolidator.LockContext", _noop_lock),
            patch("openviking.maintenance.memory_consolidator.get_lock_manager", return_value=MagicMock()),
        ):
            result = await consolidator.run(
                "viking://agent/x/memories/patterns/",
                ctx,
            )

        assert "/agent/default/" in result.audit_uri
