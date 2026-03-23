# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for MarkdownParser hard character limit enforcement (max_section_chars)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig, load_parser_configs_from_dict

# ---------------------------------------------------------------------------
# ParserConfig
# ---------------------------------------------------------------------------


class TestParserConfigMaxSectionChars:
    def test_default_value(self):
        config = ParserConfig()
        assert config.max_section_chars == 6000

    def test_custom_value(self):
        config = ParserConfig(max_section_chars=3000)
        assert config.max_section_chars == 3000

    def test_from_dict(self):
        config = ParserConfig.from_dict({"max_section_chars": 2000})
        assert config.max_section_chars == 2000

    def test_from_dict_missing_key_uses_default(self):
        config = ParserConfig.from_dict({})
        assert config.max_section_chars == 6000

    def test_from_dict_rejects_unknown_key(self):
        with pytest.raises(ValueError, match="max_section_chars"):
            ParserConfig.from_dict({"max_section_chras": 2000})

    def test_load_parser_configs_rejects_unknown_parser_section(self):
        with pytest.raises(ValueError, match="markdown"):
            load_parser_configs_from_dict({"markdwon": {}})

    def test_validate_rejects_zero(self):
        config = ParserConfig(max_section_chars=0)
        with pytest.raises(ValueError, match="max_section_chars"):
            config.validate()

    def test_validate_rejects_negative(self):
        config = ParserConfig(max_section_chars=-1)
        with pytest.raises(ValueError, match="max_section_chars"):
            config.validate()

    def test_validate_accepts_positive(self):
        config = ParserConfig(max_section_chars=1)
        config.validate()  # must not raise


# ---------------------------------------------------------------------------
# _smart_split_content — unit tests (no VikingFS needed)
# ---------------------------------------------------------------------------


class TestSmartSplitContentCharLimit:
    def _make_parser(self, max_section_chars: int = 100) -> MarkdownParser:
        """Parser with a tight char limit for easy testing."""
        config = ParserConfig(max_section_size=1000, max_section_chars=max_section_chars)
        return MarkdownParser(config=config)

    def test_short_content_returned_as_single_part(self):
        parser = self._make_parser(max_section_chars=500)
        content = "Hello world. " * 10  # 130 chars, well within limit
        parts = parser._smart_split_content(content, max_size=1000)
        assert len(parts) == 1

    def test_paragraph_exceeding_char_limit_is_force_split(self):
        """A single paragraph longer than max_section_chars must be split."""
        parser = self._make_parser(max_section_chars=50)
        # One paragraph of 200 chars — no \n\n so it stays as one para
        long_para = "x" * 200
        parts = parser._smart_split_content(long_para, max_size=1000)
        # Each part must be <= max_section_chars
        for part in parts:
            assert len(part) <= 50
        # All content is preserved
        assert "".join(p.strip() for p in parts) == long_para

    def test_accumulated_chunks_respect_char_limit(self):
        """Multiple small paragraphs that together exceed max_chars are split."""
        parser = self._make_parser(max_section_chars=80)
        # Each paragraph is 40 chars; two together (40+2+40=82) exceed the limit
        para = "y" * 40
        content = f"{para}\n\n{para}\n\n{para}"
        parts = parser._smart_split_content(content, max_size=1000)
        for part in parts:
            assert len(part) <= 80

    def test_char_limit_splits_even_when_token_estimate_is_small(self):
        """Content within token limit but exceeding char limit must still be split."""
        # Use a very large max_section_chars to NOT trigger char splitting
        parser_no_limit = self._make_parser(max_section_chars=10000)
        # Use a tight max_section_chars to force splitting
        parser_with_limit = self._make_parser(max_section_chars=50)

        content = "a" * 120  # single paragraph, ~36 estimated tokens (well under 1000)

        parts_no_limit = parser_no_limit._smart_split_content(content, max_size=1000)
        parts_with_limit = parser_with_limit._smart_split_content(content, max_size=1000)

        # Without char limit: one part (token estimate is fine)
        assert len(parts_no_limit) == 1
        # With char limit: must be split into multiple parts
        assert len(parts_with_limit) > 1
        for part in parts_with_limit:
            assert len(part) <= 50

    def test_empty_paragraphs_handled(self):
        parser = self._make_parser(max_section_chars=200)
        content = "line1\n\n\n\nline2"
        parts = parser._smart_split_content(content, max_size=1000)
        assert all(p.strip() for p in parts)  # no blank-only parts

    def test_content_entirely_preserved_after_split(self):
        """No characters should be lost during splitting."""
        parser = self._make_parser(max_section_chars=60)
        # Build content where each paragraph is 50 chars
        para = "p" * 50
        content = "\n\n".join([para] * 5)
        parts = parser._smart_split_content(content, max_size=1000)
        # Reconstruct: strip whitespace added by joining
        total_chars = sum(len(p.replace(" ", "").replace("\n", "")) for p in parts)
        original_chars = len(content.replace(" ", "").replace("\n", ""))
        assert total_chars == original_chars


# ---------------------------------------------------------------------------
# _save_section — char limit gates (async, VikingFS mocked)
# ---------------------------------------------------------------------------


class TestSaveSectionCharLimit:
    def _make_parser(self, max_section_chars: int = 100) -> MarkdownParser:
        config = ParserConfig(max_section_size=1000, max_section_chars=max_section_chars)
        return MarkdownParser(config=config)

    def _mock_viking_fs(self):
        vfs = MagicMock()
        vfs.write_file = AsyncMock()
        vfs.mkdir = AsyncMock()
        return vfs

    @pytest.mark.asyncio
    async def test_section_within_both_limits_saved_as_file(self):
        """Section under token AND char limit → single .md file."""
        parser = self._make_parser(max_section_chars=500)
        mock_vfs = self._mock_viking_fs()

        section = {
            "name": "intro",
            "tokens": 50,
            "content": "Short content",
            "has_children": False,
        }

        with patch.object(parser, "_get_viking_fs", return_value=mock_vfs):
            await parser._save_section("", [], "viking://tmp/root", section, 1000, 512)

        mock_vfs.write_file.assert_called_once()
        call_path = mock_vfs.write_file.call_args[0][0]
        assert call_path.endswith("intro.md")

    @pytest.mark.asyncio
    async def test_section_exceeding_char_limit_is_split_even_if_tokens_ok(self):
        """Section within token limit but over char limit must NOT be written as single file."""
        parser = self._make_parser(max_section_chars=50)
        mock_vfs = self._mock_viking_fs()

        long_content = "word " * 30  # 150 chars, ~14 estimated tokens (under 1000)
        section = {
            "name": "big_section",
            "tokens": 14,
            "content": long_content,
            "has_children": False,
        }

        with patch.object(parser, "_get_viking_fs", return_value=mock_vfs):
            await parser._save_section("", [], "viking://tmp/root", section, 1000, 512)

        # Must have written at least one file
        mock_vfs.write_file.assert_called()
        # Every written chunk must respect the char limit
        for call in mock_vfs.write_file.call_args_list:
            written_content = call[0][1]
            assert len(written_content) <= 50, (
                f"Written content exceeds char limit: {len(written_content)} chars"
            )
        # A directory should have been created for the split
        mock_vfs.mkdir.assert_called()


# ---------------------------------------------------------------------------
# _save_merged — char limit enforcement (async, VikingFS mocked)
# ---------------------------------------------------------------------------


class TestSaveMergedCharLimit:
    def _make_parser(self, max_section_chars: int) -> MarkdownParser:
        config = ParserConfig(max_section_size=1000, max_section_chars=max_section_chars)
        return MarkdownParser(config=config)

    def _mock_viking_fs(self):
        vfs = MagicMock()
        vfs.write_file = AsyncMock()
        return vfs

    @pytest.mark.asyncio
    async def test_merged_within_char_limit_saved_as_single_file(self):
        parser = self._make_parser(max_section_chars=500)
        mock_vfs = self._mock_viking_fs()

        sections = [("s1", "hello", 5), ("s2", "world", 5)]
        with patch.object(parser, "_get_viking_fs", return_value=mock_vfs):
            await parser._save_merged(mock_vfs, "viking://tmp/root", sections)

        mock_vfs.write_file.assert_called_once()
        path, content = mock_vfs.write_file.call_args[0]
        assert path.endswith(".md")
        assert "hello" in content and "world" in content

    @pytest.mark.asyncio
    async def test_merged_exceeding_char_limit_is_split_into_multiple_files(self):
        """When joined content exceeds max_section_chars, _save_merged must split it."""
        parser = self._make_parser(max_section_chars=60)
        mock_vfs = self._mock_viking_fs()

        # Each section is 40 chars; joined = 40 + "\n\n" + 40 = 82 chars > 60
        sections = [("a", "A" * 40, 12), ("b", "B" * 40, 12)]
        with patch.object(parser, "_get_viking_fs", return_value=mock_vfs):
            await parser._save_merged(mock_vfs, "viking://tmp/root", sections)

        # Should have written multiple files
        assert mock_vfs.write_file.call_count > 1
        for call in mock_vfs.write_file.call_args_list:
            written_content = call[0][1]
            assert len(written_content) <= 60, (
                f"Merged split part exceeds char limit: {len(written_content)} chars"
            )

    @pytest.mark.asyncio
    async def test_many_small_sections_merged_correctly_split(self):
        """Many token-small but char-large sections that accumulate past the limit."""
        parser = self._make_parser(max_section_chars=100)
        mock_vfs = self._mock_viking_fs()

        # 10 sections × 30 chars = 300 chars + separators >> 100 chars
        sections = [(f"s{i}", "z" * 30, 9) for i in range(10)]
        with patch.object(parser, "_get_viking_fs", return_value=mock_vfs):
            await parser._save_merged(mock_vfs, "viking://tmp/root", sections)

        # Every written part must respect the char limit
        mock_vfs.write_file.assert_called()
        for call in mock_vfs.write_file.call_args_list:
            written_content = call[0][1]
            assert len(written_content) <= 100, (
                f"Part exceeds char limit: {len(written_content)} chars"
            )


# ---------------------------------------------------------------------------
# _parse_and_create_structure — small-document char limit (async, VikingFS mocked)
# ---------------------------------------------------------------------------


class TestParseAndCreateStructureCharLimit:
    def _make_parser(self, max_section_size: int = 1000, max_section_chars: int = 100):
        config = ParserConfig(
            max_section_size=max_section_size, max_section_chars=max_section_chars
        )
        return MarkdownParser(config=config)

    def _mock_viking_fs(self):
        vfs = MagicMock()
        vfs.write_file = AsyncMock()
        vfs.mkdir = AsyncMock()
        return vfs

    @pytest.mark.asyncio
    async def test_small_doc_within_both_limits_saved_as_single_file(self):
        parser = self._make_parser(max_section_size=1000, max_section_chars=500)
        mock_vfs = self._mock_viking_fs()
        content = "# Title\n\nShort body."  # well under both limits

        with patch.object(parser, "_get_viking_fs", return_value=mock_vfs):
            await parser._parse_and_create_structure(content, [], "viking://tmp/root")

        mock_vfs.write_file.assert_called_once()
        written_content = mock_vfs.write_file.call_args[0][1]
        assert written_content == content

    @pytest.mark.asyncio
    async def test_small_doc_exceeding_char_limit_is_not_saved_as_single_file(self):
        """Even if token estimate is tiny, a doc over max_section_chars must be split."""
        parser = self._make_parser(max_section_size=1000, max_section_chars=30)
        mock_vfs = self._mock_viking_fs()

        # 200 chars but NO headings → no sections, falls back to paragraph split
        content = "a" * 200

        with patch.object(parser, "_get_viking_fs", return_value=mock_vfs):
            await parser._parse_and_create_structure(content, [], "viking://tmp/root")

        # Must have been split: each written chunk ≤ max_section_chars
        assert mock_vfs.write_file.call_count > 1
        for call in mock_vfs.write_file.call_args_list:
            written = call[0][1]
            assert len(written) <= 30

    @pytest.mark.asyncio
    async def test_doc_with_headings_exceeding_char_limit_is_not_saved_as_single_file(self):
        """Small-document fast-path with headings: char limit must still be enforced."""
        parser = self._make_parser(max_section_size=1000, max_section_chars=50)
        mock_vfs = self._mock_viking_fs()

        # 200 chars WITH a heading — exercises the char-check at the
        # "estimated_tokens <= max_size AND len(content) <= max_chars" guard
        content = "# Heading\n\n" + "b" * 189  # total > 50 chars, token estimate << 1000

        with patch.object(parser, "_get_viking_fs", return_value=mock_vfs):
            headings = parser._find_headings(content)
            await parser._parse_and_create_structure(content, headings, "viking://tmp/root")

        # Must not have been saved as a single file equal to the full content
        for call in mock_vfs.write_file.call_args_list:
            written = call[0][1]
            assert len(written) <= 50, (
                f"Single file written with {len(written)} chars, exceeds limit of 50"
            )
