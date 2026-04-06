# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for StatsAggregator."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.fixture
def mock_viking_fs():
    """Create a mock VikingFS that returns empty directory listings."""
    fs = AsyncMock()
    # Default: profile.md doesn't exist, all dirs are empty
    fs.exists = AsyncMock(return_value=False)
    fs.ls = AsyncMock(return_value=[])
    return fs


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
    @patch("openviking.storage.stats_aggregator.get_viking_fs")
    async def test_empty_store(self, mock_get_fs, aggregator, mock_vikingdb, mock_ctx):
        """Stats for an empty memory store should return zeros."""
        fs = AsyncMock()
        fs.exists = AsyncMock(return_value=False)
        fs.ls = AsyncMock(return_value=[])
        mock_get_fs.return_value = fs

        mock_vikingdb.query = AsyncMock(return_value=[])

        result = await aggregator.get_memory_stats(mock_ctx)

        assert result["total_memories"] == 0
        assert "total_vectors" not in result
        assert result["hotness_distribution"] == {"cold": 0, "warm": 0, "hot": 0}

    @pytest.mark.asyncio
    @patch("openviking.storage.stats_aggregator.get_viking_fs")
    async def test_counts_by_category(self, mock_get_fs, aggregator, mock_vikingdb, mock_ctx):
        """Records should be bucketed into the correct category based on filesystem."""
        now = datetime.now(timezone.utc)
        records = [
            _make_memory_record("cases", active_count=5, updated_at=now),
            _make_memory_record("cases", active_count=3, updated_at=now),
            _make_memory_record("tools", active_count=1, updated_at=now),
        ]

        fs = AsyncMock()
        fs.exists = AsyncMock(return_value=False)

        async def _ls(uri, **kwargs):
            if "/cases" in uri:
                return [
                    {"name": "mem_abc.md", "isDir": False},
                    {"name": "mem_def.md", "isDir": False},
                    {"name": ".abstract.md", "isDir": False},
                ]
            if "/tools" in uri:
                return [
                    {"name": "mem_ghi.md", "isDir": False},
                ]
            return []

        fs.ls = AsyncMock(side_effect=_ls)
        mock_get_fs.return_value = fs
        mock_vikingdb.query = AsyncMock(return_value=records)

        result = await aggregator.get_memory_stats(mock_ctx)

        # .abstract.md should be excluded, only .md files counted
        assert result["by_category"]["cases"] == 2
        assert result["by_category"]["tools"] == 1
        assert result["total_memories"] == 3

    @pytest.mark.asyncio
    @patch("openviking.storage.stats_aggregator.get_viking_fs")
    async def test_category_filter(self, mock_get_fs, aggregator, mock_vikingdb, mock_ctx):
        """Passing a category filter should only count that category."""
        now = datetime.now(timezone.utc)
        records = [
            _make_memory_record("patterns", active_count=2, updated_at=now),
        ]

        fs = AsyncMock()
        fs.exists = AsyncMock(return_value=False)
        fs.ls = AsyncMock(return_value=[
            {"name": "mem_xyz.md", "isDir": False},
        ])
        mock_get_fs.return_value = fs
        mock_vikingdb.query = AsyncMock(return_value=records)

        result = await aggregator.get_memory_stats(mock_ctx, category="patterns")

        assert "patterns" in result["by_category"]
        assert len(result["by_category"]) == 1

    @pytest.mark.asyncio
    @patch("openviking.storage.stats_aggregator.get_viking_fs")
    async def test_profile_counted(self, mock_get_fs, aggregator, mock_vikingdb, mock_ctx):
        """profile.md should be counted as 1 when it exists."""
        fs = AsyncMock()
        fs.exists = AsyncMock(return_value=True)
        fs.ls = AsyncMock(return_value=[])
        mock_get_fs.return_value = fs
        mock_vikingdb.query = AsyncMock(return_value=[])

        result = await aggregator.get_memory_stats(mock_ctx)

        assert result["by_category"]["profile"] == 1

    @pytest.mark.asyncio
    @patch("openviking.storage.stats_aggregator.get_viking_fs")
    async def test_dotfiles_excluded(self, mock_get_fs, aggregator, mock_vikingdb, mock_ctx):
        """.abstract.md and .overview.md should not be counted as memories."""
        fs = AsyncMock()
        fs.exists = AsyncMock(return_value=False)
        fs.ls = AsyncMock(return_value=[
            {"name": ".abstract.md", "isDir": False},
            {"name": ".overview.md", "isDir": False},
            {"name": "mem_real.md", "isDir": False},
        ])
        mock_get_fs.return_value = fs
        mock_vikingdb.query = AsyncMock(return_value=[])

        result = await aggregator.get_memory_stats(mock_ctx, category="entities")

        assert result["by_category"]["entities"] == 1

    @pytest.mark.asyncio
    @patch("openviking.storage.stats_aggregator.get_viking_fs")
    async def test_hotness_buckets(self, mock_get_fs, aggregator, mock_vikingdb, mock_ctx):
        """Records should be classified into cold/warm/hot based on score."""
        now = datetime.now(timezone.utc)
        hot_record = _make_memory_record("cases", active_count=50, updated_at=now)
        cold_record = _make_memory_record(
            "cases", active_count=0, updated_at=now - timedelta(days=60)
        )

        fs = AsyncMock()
        fs.exists = AsyncMock(return_value=False)
        fs.ls = AsyncMock(return_value=[
            {"name": "mem_a.md", "isDir": False},
            {"name": "mem_b.md", "isDir": False},
        ])
        mock_get_fs.return_value = fs
        mock_vikingdb.query = AsyncMock(return_value=[hot_record, cold_record])

        result = await aggregator.get_memory_stats(mock_ctx, category="cases")

        dist = result["hotness_distribution"]
        assert dist["hot"] >= 1
        assert dist["cold"] >= 1

    @pytest.mark.asyncio
    @patch("openviking.storage.stats_aggregator.get_viking_fs")
    async def test_staleness_metrics(self, mock_get_fs, aggregator, mock_vikingdb, mock_ctx):
        """Staleness should detect records not accessed in 7 and 30 days."""
        now = datetime.now(timezone.utc)
        old_record = _make_memory_record(
            "events",
            active_count=1,
            updated_at=now - timedelta(days=40),
            created_at=now - timedelta(days=50),
        )

        fs = AsyncMock()
        fs.exists = AsyncMock(return_value=False)
        fs.ls = AsyncMock(return_value=[
            {"name": "mem_old.md", "isDir": False},
        ])
        mock_get_fs.return_value = fs
        mock_vikingdb.query = AsyncMock(return_value=[old_record])

        result = await aggregator.get_memory_stats(mock_ctx, category="events")

        assert result["staleness"]["not_accessed_7d"] >= 1
        assert result["staleness"]["not_accessed_30d"] >= 1
        assert result["staleness"]["oldest_memory_age_days"] >= 49

    @pytest.mark.asyncio
    @patch("openviking.storage.stats_aggregator.get_viking_fs")
    async def test_missing_directory_returns_zero(self, mock_get_fs, aggregator, mock_vikingdb, mock_ctx):
        """If a directory doesn't exist on filesystem, the category should show 0."""
        fs = AsyncMock()
        fs.exists = AsyncMock(return_value=False)
        # ls raises for nonexistent dirs
        fs.ls = AsyncMock(side_effect=Exception("directory not found"))
        mock_get_fs.return_value = fs
        mock_vikingdb.query = AsyncMock(return_value=[])

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
