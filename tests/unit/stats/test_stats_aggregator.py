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
    ctx = MagicMock()
    ctx.user.user_id = "default"
    return ctx


@pytest.fixture
def aggregator(mock_vikingdb):
    return StatsAggregator(mock_vikingdb)


def _make_memory_record(
    category: str,
    active_count: int = 1,
    updated_at: datetime = None,
    created_at: datetime = None,
):
    """Helper to build a mock memory record with a realistic URI."""
    now = datetime.now(timezone.utc)
    if category == "profile":
        uri = "viking://user/default/memories/profile.md"
    else:
        uri = f"viking://user/default/memories/{category}/test-item"
    return {
        "uri": uri,
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
        """Records should be bucketed into the correct category from their URI."""
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
        """Passing a category filter should only count that category."""
        now = datetime.now(timezone.utc)
        records = [
            _make_memory_record("patterns", active_count=2, updated_at=now),
        ]

        mock_vikingdb.query = AsyncMock(return_value=records)

        result = await aggregator.get_memory_stats(mock_ctx, category="patterns")

        assert "patterns" in result["by_category"]
        assert len(result["by_category"]) == 1
        assert result["total_memories"] == 1

    @pytest.mark.asyncio
    async def test_profile_counted(self, aggregator, mock_vikingdb, mock_ctx):
        """profile.md should be counted as 1 when present in query results."""
        records = [
            _make_memory_record("profile", active_count=0),
        ]
        mock_vikingdb.query = AsyncMock(return_value=records)

        result = await aggregator.get_memory_stats(mock_ctx)

        assert result["by_category"]["profile"] == 1
        assert result["total_memories"] == 1

    @pytest.mark.asyncio
    async def test_unrecognized_uri_ignored(self, aggregator, mock_vikingdb, mock_ctx):
        """Records with unrecognized URIs should not be counted in any category."""
        now = datetime.now(timezone.utc)
        records = [
            {
                "uri": "viking://some/random/path",
                "context_type": "memory",
                "active_count": 1,
                "updated_at": now.isoformat(),
                "created_at": now.isoformat(),
            }
        ]
        mock_vikingdb.query = AsyncMock(return_value=records)

        result = await aggregator.get_memory_stats(mock_ctx)

        assert result["total_memories"] == 0
        for cat in result["by_category"]:
            assert result["by_category"][cat] == 0

    @pytest.mark.asyncio
    async def test_hotness_buckets(self, aggregator, mock_vikingdb, mock_ctx):
        """Records should be classified into cold/warm/hot based on score."""
        now = datetime.now(timezone.utc)
        hot_record = _make_memory_record("cases", active_count=50, updated_at=now)
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
    async def test_category_filter_excludes_other_records_from_metrics(
        self, aggregator, mock_vikingdb, mock_ctx
    ):
        """When a category filter is applied, hotness/staleness should only
        count records that match the filter, even if the query returns
        records from other categories.
        """
        now = datetime.now(timezone.utc)
        records = [
            _make_memory_record("cases", active_count=50, updated_at=now),
            _make_memory_record(
                "tools", active_count=0, updated_at=now - timedelta(days=60)
            ),
        ]
        mock_vikingdb.query = AsyncMock(return_value=records)

        result = await aggregator.get_memory_stats(mock_ctx, category="cases")

        assert result["by_category"]["cases"] == 1
        assert result["total_memories"] == 1
        # Only the "cases" record should contribute to hotness
        assert result["hotness_distribution"]["hot"] == 1
        assert result["hotness_distribution"]["cold"] == 0
        # Only the "cases" record should contribute to staleness
        assert result["staleness"]["not_accessed_7d"] == 0
        assert result["staleness"]["not_accessed_30d"] == 0

    @pytest.mark.asyncio
    async def test_query_error_returns_zeros(self, aggregator, mock_vikingdb, mock_ctx):
        """If the vector query fails, stats should gracefully return zeros."""
        mock_vikingdb.query = AsyncMock(side_effect=Exception("db down"))

        result = await aggregator.get_memory_stats(mock_ctx, category="cases")

        assert result["by_category"]["cases"] == 0
        assert result["total_memories"] == 0
        assert result["hotness_distribution"] == {"cold": 0, "warm": 0, "hot": 0}


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
