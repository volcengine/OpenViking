# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for StatsAggregator."""

from datetime import datetime, timedelta, timezone
from random import Random
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.stats_aggregator import StatsAggregator, _parse_datetime
from openviking.storage.vectordb import engine
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@pytest.fixture
def mock_vikingdb():
    """Create a mock VikingDB manager."""
    return AsyncMock()


@pytest.fixture
def mock_ctx():
    """Create a mock request context."""
    return MagicMock()


@pytest.fixture
def aggregator(mock_vikingdb):
    return StatsAggregator(mock_vikingdb)


def _make_memory_record(
    category: str,
    active_count: int = 1,
    updated_at: datetime = None,
    created_at: datetime = None,
):
    """Helper to build a mock memory record."""
    now = datetime.now(timezone.utc)
    return {
        "uri": f"viking://memories/{category}/test-item",
        "context_type": "memory",
        "active_count": active_count,
        "updated_at": (updated_at or now).isoformat(),
        "created_at": (created_at or now).isoformat(),
    }


class TestStatsAggregator:
    @pytest.mark.asyncio
    async def test_empty_store(self, aggregator, mock_vikingdb, mock_ctx):
        """Stats for an empty memory store should return zeros."""
        mock_vikingdb.filter = AsyncMock(return_value=[])

        result = await aggregator.get_memory_stats(mock_ctx)

        assert result["total_memories"] == 0
        assert "total_vectors" not in result
        assert result["hotness_distribution"] == {"cold": 0, "warm": 0, "hot": 0}

    @pytest.mark.asyncio
    async def test_counts_by_category(self, aggregator, mock_vikingdb, mock_ctx):
        """Records should be bucketed into the correct category."""
        now = datetime.now(timezone.utc)
        records = [
            _make_memory_record("cases", active_count=5, updated_at=now),
            _make_memory_record("cases", active_count=3, updated_at=now),
            _make_memory_record("tools", active_count=1, updated_at=now),
        ]
        mock_vikingdb.filter = AsyncMock(return_value=records)

        result = await aggregator.get_memory_stats(mock_ctx)

        assert result["by_category"]["cases"] == 2
        assert result["by_category"]["tools"] == 1
        assert result["total_memories"] == 3

    @pytest.mark.asyncio
    async def test_category_filter(self, aggregator, mock_vikingdb, mock_ctx):
        """Passing a category filter should only query that category."""
        now = datetime.now(timezone.utc)
        records = [
            _make_memory_record("patterns", active_count=2, updated_at=now),
        ]
        mock_vikingdb.filter = AsyncMock(return_value=records)

        result = await aggregator.get_memory_stats(mock_ctx, category="patterns")

        assert "patterns" in result["by_category"]
        assert len(result["by_category"]) == 1

    @pytest.mark.asyncio
    async def test_hotness_buckets(self, aggregator, mock_vikingdb, mock_ctx):
        """Records should be classified into cold/warm/hot based on score."""
        now = datetime.now(timezone.utc)
        # Recent + high access -> hot
        hot_record = _make_memory_record("cases", active_count=50, updated_at=now)
        # Old + no access -> cold
        cold_record = _make_memory_record(
            "cases", active_count=0, updated_at=now - timedelta(days=60)
        )
        mock_vikingdb.filter = AsyncMock(return_value=[hot_record, cold_record])

        result = await aggregator.get_memory_stats(mock_ctx, category="cases")

        dist = result["hotness_distribution"]
        assert dist["hot"] >= 1
        assert dist["cold"] >= 1

    @pytest.mark.asyncio
    async def test_staleness_metrics(self, aggregator, mock_vikingdb, mock_ctx):
        """Staleness should detect records not accessed in 7 and 30 days."""
        now = datetime.now(timezone.utc)
        old_record = _make_memory_record(
            "events",
            active_count=1,
            updated_at=now - timedelta(days=40),
            created_at=now - timedelta(days=50),
        )
        mock_vikingdb.filter = AsyncMock(return_value=[old_record])

        result = await aggregator.get_memory_stats(mock_ctx, category="events")

        assert result["staleness"]["not_accessed_7d"] >= 1
        assert result["staleness"]["not_accessed_30d"] >= 1
        assert result["staleness"]["oldest_memory_age_days"] >= 49

    @pytest.mark.asyncio
    async def test_query_error_returns_empty(self, aggregator, mock_vikingdb, mock_ctx):
        """If VikingDB query fails, the category should show 0 records."""
        mock_vikingdb.filter = AsyncMock(side_effect=Exception("connection error"))

        result = await aggregator.get_memory_stats(mock_ctx, category="cases")

        assert result["by_category"]["cases"] == 0
        assert result["total_memories"] == 0

    @pytest.mark.asyncio
    async def test_query_all_memories_paginates_past_the_single_query_cap(
        self, vector_backend_factory, tmp_path, monkeypatch
    ):
        """Count all memories when a shared timestamp spans multiple index pages."""
        if not getattr(engine, "PersistStore", None):
            pytest.skip("local persistent vectordb engine is not available")

        monkeypatch.setattr(
            "openviking.storage.viking_vector_index_backend.init_cpp_logging", lambda: None
        )
        monkeypatch.setattr("openviking.storage.vectordb_adapters.base.random", Random(1))
        backend = vector_backend_factory(
            config=VectorDBBackendConfig(
                backend="local", name="context", dimension=4, path=str(tmp_path)
            )
        )
        ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)
        timestamp = "2025-01-01T00:00:00Z"
        records = []
        for index in range(10_050):
            category = "events" if index < 10_000 else "profile"
            records.append(
                {
                    "id": str(index),
                    "uri": f"viking://user/alice/memories/{category}/{index}.md",
                    "context_type": "memory",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "vector": [1.0 if category == "events" else -1.0, 0.0, 0.0, 0.0],
                }
            )
        try:
            assert await backend.create_collection(
                "context", CollectionSchemas.context_collection("context", 4)
            )
            await backend.upsert_many(records, ctx=ctx)
            result = await StatsAggregator(backend).get_memory_stats(ctx)
        finally:
            await backend.close()

        assert result["total_memories"] == 10_050
        assert result["by_category"]["events"] == 10_000
        assert result["by_category"]["profile"] == 50


class TestParseDatetime:
    def test_none(self):
        assert _parse_datetime(None) is None

    def test_datetime_object(self):
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert _parse_datetime(dt) == dt

    def test_naive_datetime(self):
        dt = datetime(2025, 1, 1)
        result = _parse_datetime(dt)
        assert result.tzinfo == timezone.utc

    def test_iso_string(self):
        result = _parse_datetime("2025-01-01T00:00:00Z")
        assert result is not None
        assert result.year == 2025

    def test_invalid_string(self):
        assert _parse_datetime("not-a-date") is None

    def test_integer(self):
        assert _parse_datetime(12345) is None
