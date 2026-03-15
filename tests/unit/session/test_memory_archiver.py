# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for memory cold-storage archival."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from openviking.session.memory_archiver import (
    ArchivalCandidate,
    MemoryArchiver,
    _build_archive_uri,
    _build_restore_uri,
    _parse_datetime,
)

# ---------------------------------------------------------------------------
# Helper URI functions
# ---------------------------------------------------------------------------


class TestBuildArchiveUri:
    def test_simple_file(self):
        assert (
            _build_archive_uri("viking://memories/facts/greeting.md")
            == "viking://memories/facts/_archive/greeting.md"
        )

    def test_nested_path(self):
        assert (
            _build_archive_uri("viking://memories/user/prefs/theme.md")
            == "viking://memories/user/prefs/_archive/theme.md"
        )

    def test_root_level_file(self):
        assert (
            _build_archive_uri("viking://memories/note.md") == "viking://memories/_archive/note.md"
        )

    def test_no_slash(self):
        assert _build_archive_uri("note.md") == "_archive/note.md"


class TestBuildRestoreUri:
    def test_simple_restore(self):
        assert (
            _build_restore_uri("viking://memories/facts/_archive/greeting.md")
            == "viking://memories/facts/greeting.md"
        )

    def test_nested_restore(self):
        assert (
            _build_restore_uri("viking://memories/user/_archive/pref.md")
            == "viking://memories/user/pref.md"
        )

    def test_not_archived_returns_none(self):
        assert _build_restore_uri("viking://memories/facts/greeting.md") is None

    def test_roundtrip(self):
        original = "viking://memories/deep/path/to/file.md"
        archived = _build_archive_uri(original)
        restored = _build_restore_uri(archived)
        assert restored == original


# ---------------------------------------------------------------------------
# Datetime parsing
# ---------------------------------------------------------------------------


class TestParseDatetime:
    def test_none(self):
        assert _parse_datetime(None) is None

    def test_datetime_object(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert _parse_datetime(dt) == dt

    def test_naive_datetime_gets_utc(self):
        dt = datetime(2026, 1, 1)
        result = _parse_datetime(dt)
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_iso_string(self):
        result = _parse_datetime("2026-01-01T00:00:00+00:00")
        assert result is not None
        assert result.year == 2026

    def test_invalid_string(self):
        assert _parse_datetime("not-a-date") is None

    def test_integer_returns_none(self):
        assert _parse_datetime(12345) is None


# ---------------------------------------------------------------------------
# MemoryArchiver.scan
# ---------------------------------------------------------------------------


def _make_storage(records):
    """Create a mock storage that returns records from scroll()."""
    storage = AsyncMock()
    storage.scroll = AsyncMock(return_value=(records, None))
    return storage


def _make_viking_fs():
    """Create a mock VikingFS."""
    vfs = AsyncMock()
    vfs.mv = AsyncMock(return_value={"status": "ok"})
    return vfs


NOW = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
OLD_DATE = NOW - timedelta(days=30)
RECENT_DATE = NOW - timedelta(days=2)


class TestScan:
    @pytest.mark.asyncio
    async def test_scan_finds_cold_memories(self):
        records = [
            {
                "uri": "viking://memories/fact1.md",
                "active_count": 0,
                "updated_at": OLD_DATE,
                "context_type": "memory",
                "parent_uri": "viking://memories/",
            },
        ]
        archiver = MemoryArchiver(
            viking_fs=_make_viking_fs(),
            storage=_make_storage(records),
            threshold=0.5,
            min_age_days=7,
        )
        candidates = await archiver.scan("viking://memories/", now=NOW)
        assert len(candidates) == 1
        assert candidates[0].uri == "viking://memories/fact1.md"
        assert candidates[0].score < 0.5

    @pytest.mark.asyncio
    async def test_scan_skips_recent_memories(self):
        records = [
            {
                "uri": "viking://memories/recent.md",
                "active_count": 0,
                "updated_at": RECENT_DATE,
                "context_type": "memory",
                "parent_uri": "viking://memories/",
            },
        ]
        archiver = MemoryArchiver(
            viking_fs=_make_viking_fs(),
            storage=_make_storage(records),
            threshold=0.5,
            min_age_days=7,
        )
        candidates = await archiver.scan("viking://memories/", now=NOW)
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_scan_skips_already_archived(self):
        records = [
            {
                "uri": "viking://memories/_archive/old.md",
                "active_count": 0,
                "updated_at": OLD_DATE,
                "context_type": "memory",
                "parent_uri": "viking://memories/_archive/",
            },
        ]
        archiver = MemoryArchiver(
            viking_fs=_make_viking_fs(),
            storage=_make_storage(records),
            threshold=0.5,
            min_age_days=7,
        )
        candidates = await archiver.scan("viking://memories/", now=NOW)
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_scan_skips_out_of_scope(self):
        records = [
            {
                "uri": "viking://resources/doc.md",
                "active_count": 0,
                "updated_at": OLD_DATE,
                "context_type": "resource",
                "parent_uri": "viking://resources/",
            },
        ]
        archiver = MemoryArchiver(
            viking_fs=_make_viking_fs(),
            storage=_make_storage(records),
            threshold=0.5,
            min_age_days=7,
        )
        candidates = await archiver.scan("viking://memories/", now=NOW)
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_scan_keeps_hot_memories(self):
        records = [
            {
                "uri": "viking://memories/hot.md",
                "active_count": 100,
                "updated_at": NOW - timedelta(days=1),
                "context_type": "memory",
                "parent_uri": "viking://memories/",
            },
        ]
        archiver = MemoryArchiver(
            viking_fs=_make_viking_fs(),
            storage=_make_storage(records),
            threshold=0.5,
            min_age_days=0,
        )
        candidates = await archiver.scan("viking://memories/", now=NOW)
        # High active_count + recent = hot, should not be a candidate
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_scan_sorts_coldest_first(self):
        records = [
            {
                "uri": "viking://memories/warm.md",
                "active_count": 5,
                "updated_at": OLD_DATE,
                "context_type": "memory",
                "parent_uri": "viking://memories/",
            },
            {
                "uri": "viking://memories/cold.md",
                "active_count": 0,
                "updated_at": OLD_DATE - timedelta(days=60),
                "context_type": "memory",
                "parent_uri": "viking://memories/",
            },
        ]
        archiver = MemoryArchiver(
            viking_fs=_make_viking_fs(),
            storage=_make_storage(records),
            threshold=0.5,
            min_age_days=7,
        )
        candidates = await archiver.scan("viking://memories/", now=NOW)
        assert len(candidates) == 2
        assert candidates[0].uri == "viking://memories/cold.md"
        assert candidates[0].score <= candidates[1].score

    @pytest.mark.asyncio
    async def test_scan_empty_store(self):
        archiver = MemoryArchiver(
            viking_fs=_make_viking_fs(),
            storage=_make_storage([]),
            threshold=0.5,
            min_age_days=7,
        )
        candidates = await archiver.scan("viking://memories/", now=NOW)
        assert candidates == []


# ---------------------------------------------------------------------------
# MemoryArchiver.archive
# ---------------------------------------------------------------------------


class TestArchive:
    @pytest.mark.asyncio
    async def test_archive_moves_files(self):
        vfs = _make_viking_fs()
        archiver = MemoryArchiver(viking_fs=vfs, storage=_make_storage([]))
        candidates = [
            ArchivalCandidate(
                uri="viking://memories/fact1.md",
                active_count=0,
                updated_at=OLD_DATE,
                score=0.01,
            ),
        ]
        result = await archiver.archive(candidates)
        assert result.archived == 1
        assert result.errors == 0
        vfs.mv.assert_called_once_with(
            "viking://memories/fact1.md",
            "viking://memories/_archive/fact1.md",
            ctx=None,
        )

    @pytest.mark.asyncio
    async def test_archive_dry_run(self):
        vfs = _make_viking_fs()
        archiver = MemoryArchiver(viking_fs=vfs, storage=_make_storage([]))
        candidates = [
            ArchivalCandidate(
                uri="viking://memories/fact1.md",
                active_count=0,
                updated_at=OLD_DATE,
                score=0.01,
            ),
        ]
        result = await archiver.archive(candidates, dry_run=True)
        assert result.archived == 0
        assert result.skipped == 1
        vfs.mv.assert_not_called()

    @pytest.mark.asyncio
    async def test_archive_handles_mv_error(self):
        vfs = _make_viking_fs()
        vfs.mv = AsyncMock(side_effect=RuntimeError("AGFS error"))
        archiver = MemoryArchiver(viking_fs=vfs, storage=_make_storage([]))
        candidates = [
            ArchivalCandidate(
                uri="viking://memories/fact1.md",
                active_count=0,
                updated_at=OLD_DATE,
                score=0.01,
            ),
        ]
        result = await archiver.archive(candidates)
        assert result.archived == 0
        assert result.errors == 1

    @pytest.mark.asyncio
    async def test_archive_empty_candidates(self):
        archiver = MemoryArchiver(
            viking_fs=_make_viking_fs(),
            storage=_make_storage([]),
        )
        result = await archiver.archive([])
        assert result.archived == 0
        assert result.scanned == 0


# ---------------------------------------------------------------------------
# MemoryArchiver.restore
# ---------------------------------------------------------------------------


class TestRestore:
    @pytest.mark.asyncio
    async def test_restore_moves_back(self):
        vfs = _make_viking_fs()
        archiver = MemoryArchiver(viking_fs=vfs, storage=_make_storage([]))
        ok = await archiver.restore("viking://memories/_archive/fact1.md")
        assert ok is True
        vfs.mv.assert_called_once_with(
            "viking://memories/_archive/fact1.md",
            "viking://memories/fact1.md",
            ctx=None,
        )

    @pytest.mark.asyncio
    async def test_restore_non_archived_uri(self):
        vfs = _make_viking_fs()
        archiver = MemoryArchiver(viking_fs=vfs, storage=_make_storage([]))
        ok = await archiver.restore("viking://memories/fact1.md")
        assert ok is False
        vfs.mv.assert_not_called()

    @pytest.mark.asyncio
    async def test_restore_handles_error(self):
        vfs = _make_viking_fs()
        vfs.mv = AsyncMock(side_effect=RuntimeError("AGFS error"))
        archiver = MemoryArchiver(viking_fs=vfs, storage=_make_storage([]))
        ok = await archiver.restore("viking://memories/_archive/fact1.md")
        assert ok is False


# ---------------------------------------------------------------------------
# scan_and_archive convenience
# ---------------------------------------------------------------------------


class TestScanAndArchive:
    @pytest.mark.asyncio
    async def test_scan_and_archive(self):
        records = [
            {
                "uri": "viking://memories/cold.md",
                "active_count": 0,
                "updated_at": OLD_DATE,
                "context_type": "memory",
                "parent_uri": "viking://memories/",
            },
        ]
        vfs = _make_viking_fs()
        archiver = MemoryArchiver(
            viking_fs=vfs,
            storage=_make_storage(records),
            threshold=0.5,
            min_age_days=7,
        )
        result = await archiver.scan_and_archive("viking://memories/", now=NOW)
        assert result.archived == 1

    @pytest.mark.asyncio
    async def test_scan_and_archive_dry_run(self):
        records = [
            {
                "uri": "viking://memories/cold.md",
                "active_count": 0,
                "updated_at": OLD_DATE,
                "context_type": "memory",
                "parent_uri": "viking://memories/",
            },
        ]
        vfs = _make_viking_fs()
        archiver = MemoryArchiver(
            viking_fs=vfs,
            storage=_make_storage(records),
            threshold=0.5,
            min_age_days=7,
        )
        result = await archiver.scan_and_archive("viking://memories/", dry_run=True, now=NOW)
        assert result.archived == 0
        assert result.skipped == 1
        vfs.mv.assert_not_called()
