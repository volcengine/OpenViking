# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for StatsAggregator."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.stats_aggregator import StatsAggregator, _parse_datetime


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
        mock_vikingdb.query = AsyncMock(return_value=[])

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
        mock_vikingdb.query = AsyncMock(return_value=records)

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
        mock_vikingdb.query = AsyncMock(return_value=records)

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
        mock_vikingdb.query = AsyncMock(return_value=[hot_record, cold_record])

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
        mock_vikingdb.query = AsyncMock(return_value=[old_record])

        result = await aggregator.get_memory_stats(mock_ctx, category="events")

        assert result["staleness"]["not_accessed_7d"] >= 1
        assert result["staleness"]["not_accessed_30d"] >= 1
        assert result["staleness"]["oldest_memory_age_days"] >= 49

    @pytest.mark.asyncio
    async def test_query_error_returns_empty(self, aggregator, mock_vikingdb, mock_ctx):
        """If VikingDB query fails, the category should show 0 records."""
        mock_vikingdb.query = AsyncMock(side_effect=Exception("connection error"))

        result = await aggregator.get_memory_stats(mock_ctx, category="cases")

        assert result["by_category"]["cases"] == 0
        assert result["total_memories"] == 0


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
