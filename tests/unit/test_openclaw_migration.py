# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the OpenClaw → OpenViking migration tool.

These tests exercise pure-Python helpers (file classifier, abstract builder)
without requiring an OpenViking server or any network calls.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# The migration module lives under examples/ which is not on sys.path by
# default.  We add it here so the import works without installing anything.
# ---------------------------------------------------------------------------
_EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples" / "openclaw-migration"
if str(_EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES_DIR))

from migrate import (  # noqa: E402
    MemFile,
    build_abstract,
    build_overview,
    classify_file,
    discover_files,
)


# ===========================================================================
# File classification
# ===========================================================================


class TestClassifyFile:
    def test_classify_memory_md_upper(self, tmp_path):
        """MEMORY.md maps to entities."""
        p = tmp_path / "MEMORY.md"
        p.touch()
        assert classify_file(p) == "entities"

    def test_classify_memory_md_lower(self, tmp_path):
        """memory.md (lowercase) maps to entities."""
        p = tmp_path / "memory.md"
        p.touch()
        assert classify_file(p) == "entities"

    def test_classify_daily_log(self, tmp_path):
        """YYYY-MM-DD.md maps to events."""
        p = tmp_path / "2026-03-15.md"
        p.touch()
        assert classify_file(p) == "events"

    def test_classify_daily_log_various_dates(self, tmp_path):
        """Multiple valid date filenames all map to events."""
        dates = ["2024-01-01.md", "2025-12-31.md", "2026-04-07.md"]
        for name in dates:
            p = tmp_path / name
            p.touch()
            assert classify_file(p) == "events", f"Expected events for {name}"

    def test_classify_session_summary(self, tmp_path):
        """YYYY-MM-DD-slug.md maps to cases."""
        p = tmp_path / "2026-03-15-bug-fix.md"
        p.touch()
        assert classify_file(p) == "cases"

    def test_classify_session_summary_multi_word_slug(self, tmp_path):
        """YYYY-MM-DD-multi-word-slug.md still maps to cases."""
        p = tmp_path / "2026-03-15-api-design-review.md"
        p.touch()
        assert classify_file(p) == "cases"

    def test_classify_unknown_defaults_to_entities(self, tmp_path):
        """Any other .md filename falls back to entities."""
        p = tmp_path / "random-notes.md"
        p.touch()
        assert classify_file(p) == "entities"

    def test_classify_category_override(self, tmp_path):
        """--category flag overrides all classification rules."""
        for name in ("MEMORY.md", "2026-04-07.md", "2026-04-07-notes.md", "other.md"):
            p = tmp_path / name
            p.touch()
            assert classify_file(p, category_override="preferences") == "preferences"

    def test_classify_category_override_events(self, tmp_path):
        """Override to events works for a root memory file."""
        p = tmp_path / "MEMORY.md"
        p.touch()
        assert classify_file(p, category_override="events") == "events"


# ===========================================================================
# Abstract builder
# ===========================================================================


class TestBuildAbstract:
    def test_build_abstract_first_line(self):
        """abstract = first non-empty line."""
        content = "# My Project Notes\n\nSome details here."
        assert build_abstract(content) == "# My Project Notes"

    def test_build_abstract_skips_leading_blank_lines(self):
        """Leading blank lines are skipped; first non-empty line is used."""
        content = "\n\n\nActual first line\nSecond line"
        assert build_abstract(content) == "Actual first line"

    def test_build_abstract_truncated_to_100(self):
        """abstract is capped at 100 characters."""
        long_line = "A" * 150
        assert build_abstract(long_line) == "A" * 100

    def test_build_abstract_fallback_all_blank(self):
        """If content is only whitespace/blank lines, use first 100 raw chars."""
        content = "   \n\n   \n\nSome content here"
        result = build_abstract(content)
        # Should skip whitespace-only lines and find "Some content here"
        assert result == "Some content here"

    def test_build_abstract_empty_string(self):
        """Empty content returns empty string (no crash)."""
        assert build_abstract("") == ""

    def test_build_abstract_single_line_no_newline(self):
        """Single line without trailing newline works correctly."""
        assert build_abstract("Hello world") == "Hello world"


# ===========================================================================
# Overview builder
# ===========================================================================


class TestBuildOverview:
    def test_overview_first_five_lines(self):
        """overview = first 5 non-empty lines joined."""
        lines = [f"Line {i}" for i in range(1, 9)]
        content = "\n".join(lines)
        overview = build_overview(content)
        assert overview == "\n".join(lines[:5])

    def test_overview_truncated_to_500(self):
        """overview is capped at 500 characters."""
        long_line = "A" * 600
        assert len(build_overview(long_line)) <= 500

    def test_overview_skips_blank_lines(self):
        """Blank lines are ignored when collecting up to 5 lines."""
        content = "First\n\nSecond\n\nThird\n\nFourth\n\nFifth\n\nSixth"
        overview = build_overview(content)
        assert "Sixth" not in overview
        assert "First" in overview and "Fifth" in overview


# ===========================================================================
# File discovery
# ===========================================================================


class TestDiscoverFiles:
    def test_discovers_memory_md(self, tmp_path):
        """MEMORY.md at workspace root is discovered."""
        (tmp_path / "MEMORY.md").write_text("# Root memory")
        files = discover_files(tmp_path, None)
        names = [f.path.name for f in files]
        assert "MEMORY.md" in names

    def test_discovers_memory_subdir(self, tmp_path):
        """Files under memory/ subdirectory are discovered."""
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "2026-04-01.md").write_text("April log")
        files = discover_files(tmp_path, None)
        names = [f.path.name for f in files]
        assert "2026-04-01.md" in names

    def test_empty_dir_returns_nothing(self, tmp_path):
        """An empty workspace yields no files."""
        assert discover_files(tmp_path, None) == []

    def test_nonexistent_dir_returns_nothing(self, tmp_path):
        """A missing workspace path returns an empty list."""
        missing = tmp_path / "does-not-exist"
        assert discover_files(missing, None) == []

    def test_category_override_applied(self, tmp_path):
        """All discovered files get the override category."""
        (tmp_path / "MEMORY.md").write_text("# mem")
        mem = tmp_path / "memory"
        mem.mkdir()
        (mem / "2026-04-07.md").write_text("daily")
        files = discover_files(tmp_path, "preferences")
        assert all(f.category == "preferences" for f in files)


# ===========================================================================
# Dry-run — no writes
# ===========================================================================


class TestDryRun:
    def test_dry_run_no_writes(self, tmp_path, monkeypatch):
        """--dry-run flag must not call _migrate_async (zero writes)."""
        import migrate

        (tmp_path / "MEMORY.md").write_text("# hello")

        # Patch sys.argv so _parse_args() picks up --dry-run and the tmp dir
        monkeypatch.setattr(
            sys,
            "argv",
            ["migrate.py", "--openclaw-dir", str(tmp_path), "--dry-run"],
        )

        # Patch asyncio.run — if main() calls it, _migrate_async was reached
        with patch("asyncio.run") as mock_run:
            migrate.main()

        mock_run.assert_not_called()

    def test_dry_run_output_lists_files(self, tmp_path, capsys):
        """--dry-run prints each file with its category and char count."""
        from migrate import print_dry_run

        (tmp_path / "MEMORY.md").write_text("curated knowledge")
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-04-07.md").write_text("daily log entry")

        files = discover_files(tmp_path, None)
        print_dry_run(files, tmp_path)

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "entities" in out
        assert "events" in out
        assert "0 LLM calls" in out
