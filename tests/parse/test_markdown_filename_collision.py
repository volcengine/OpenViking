# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for _generate_merged_filename uniqueness when headings collide."""

from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig


class TestGenerateMergedFilenameCollision:
    def _make_parser(self) -> MarkdownParser:
        return MarkdownParser(ParserConfig())

    def test_duplicate_heading_produces_unique_filenames(self):
        """Merge groups with same first heading but different content must get unique filenames."""
        parser = self._make_parser()

        group1 = [
            ("Our Culture", "content about values", 1),
            ("Our Culture", "content about mission", 2),
        ]
        group2 = [
            ("Our Culture", "content about team", 3),
            ("Our Culture", "content about vision", 4),
        ]

        name1 = parser._generate_merged_filename(group1)
        name2 = parser._generate_merged_filename(group2)

        assert name1 != name2, f"Filenames must be unique but both are '{name1}'"
        assert "Our" in name1 or "Culture" in name1  # Still human-readable

    def test_single_section_filename_still_works(self):
        """Single section should still produce a readable filename."""
        parser = self._make_parser()

        sections = [("Introduction", "some content", 1)]
        name = parser._generate_merged_filename(sections)

        assert "Introduction" in name

    def test_empty_sections_returns_merged(self):
        """Empty sections list should return 'merged'."""
        parser = self._make_parser()
        assert parser._generate_merged_filename([]) == "merged"
