# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for markitdown-inspired parsers."""

import zipfile

import pytest

from openviking.parse.parsers.audio import AudioParser
from openviking.parse.parsers.epub import EPubParser
from openviking.parse.parsers.excel import ExcelParser
from openviking.parse.parsers.powerpoint import PowerPointParser
from openviking.parse.parsers.word import WordParser
from openviking.parse.parsers.zip_parser import ZipParser

# -----------------------------------------------------------------------------
# Word Parser Tests
# -----------------------------------------------------------------------------


class TestWordParser:
    """Test Word (.docx) parser."""

    @pytest.fixture
    def word_parser(self):
        return WordParser()

    def test_supported_extensions(self, word_parser):
        assert ".docx" in word_parser.supported_extensions

    @pytest.mark.asyncio
    async def test_parse_content_delegates_to_markdown(self, word_parser):
        """Test that parse_content delegates to MarkdownParser."""
        content = "# Test Heading\n\nThis is test content."
        result = await word_parser.parse_content(content, source_path="test.docx")

        assert result.source_format == "docx"
        assert result.parser_name == "WordParser"

    @pytest.mark.asyncio
    async def test_parse_nonexistent_file_falls_back(self, word_parser):
        """Test that parse() with non-existent path treats source as content."""
        result = await word_parser.parse("# Heading\n\nSome content")
        assert result.source_format == "docx"
        assert result.parser_name == "WordParser"


# -----------------------------------------------------------------------------
# PowerPoint Parser Tests
# -----------------------------------------------------------------------------


class TestPowerPointParser:
    """Test PowerPoint (.pptx) parser."""

    @pytest.fixture
    def ppt_parser(self):
        return PowerPointParser()

    def test_supported_extensions(self, ppt_parser):
        assert ".pptx" in ppt_parser.supported_extensions

    @pytest.mark.asyncio
    async def test_parse_content_delegates_to_markdown(self, ppt_parser):
        """Test that parse_content delegates to MarkdownParser."""
        content = "# Slide 1\n\nContent here."
        result = await ppt_parser.parse_content(content, source_path="test.pptx")

        assert result.source_format == "pptx"
        assert result.parser_name == "PowerPointParser"


# -----------------------------------------------------------------------------
# Excel Parser Tests
# -----------------------------------------------------------------------------


class TestExcelParser:
    """Test Excel (.xlsx) parser."""

    @pytest.fixture
    def excel_parser(self):
        return ExcelParser()

    def test_supported_extensions(self, excel_parser):
        assert ".xlsx" in excel_parser.supported_extensions
        assert ".xls" in excel_parser.supported_extensions
        assert ".xlsm" in excel_parser.supported_extensions

    @pytest.mark.asyncio
    async def test_parse_content_delegates_to_markdown(self, excel_parser):
        """Test that parse_content delegates to MarkdownParser."""
        content = "| Col1 | Col2 |\n|------|------|\n| A | B |"
        result = await excel_parser.parse_content(content, source_path="test.xlsx")

        assert result.source_format == "xlsx"
        assert result.parser_name == "ExcelParser"


# -----------------------------------------------------------------------------
# EPUB Parser Tests
# -----------------------------------------------------------------------------


class TestEPubParser:
    """Test EPUB parser."""

    @pytest.fixture
    def epub_parser(self):
        return EPubParser()

    def test_supported_extensions(self, epub_parser):
        assert ".epub" in epub_parser.supported_extensions

    @pytest.mark.asyncio
    async def test_parse_content_delegates_to_markdown(self, epub_parser):
        """Test that parse_content delegates to MarkdownParser."""
        content = "# Chapter 1\n\nOnce upon a time..."
        result = await epub_parser.parse_content(content, source_path="test.epub")

        assert result.source_format == "epub"
        assert result.parser_name == "EPubParser"


# -----------------------------------------------------------------------------
# ZIP Parser Tests
# -----------------------------------------------------------------------------


class TestZipParser:
    """Test ZIP archive parser."""

    @pytest.fixture
    def zip_parser(self):
        return ZipParser()

    @pytest.fixture
    def sample_zip(self, tmp_path):
        """Create a sample ZIP file for testing."""
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("readme.txt", "This is a readme file.")
            zf.writestr("data/info.json", '{"key": "value"}')
            zf.writestr("src/main.py", "print('hello')")
        return zip_path

    def test_supported_extensions(self, zip_parser):
        assert ".zip" in zip_parser.supported_extensions

    def test_convert_zip_to_markdown(self, zip_parser, sample_zip):
        """Test ZIP to markdown conversion."""
        markdown = zip_parser._convert_zip_to_markdown(sample_zip)

        assert "# ZIP Archive:" in markdown
        assert "readme.txt" in markdown
        assert "info.json" in markdown
        assert "main.py" in markdown

    def test_format_size(self, zip_parser):
        """Test file size formatting."""
        assert zip_parser._format_size(500) == "500.0 B"
        assert zip_parser._format_size(2048) == "2.0 KB"
        assert zip_parser._format_size(2097152) == "2.0 MB"

    def test_group_files_by_extension(self, zip_parser):
        """Test file grouping by extension."""
        filenames = ["file.txt", "data.json", "script.py", "notes.txt"]
        groups = zip_parser._group_files_by_extension(filenames)

        assert groups[".txt"] == ["file.txt", "notes.txt"]
        assert groups[".json"] == ["data.json"]
        assert groups[".py"] == ["script.py"]

    @pytest.mark.asyncio
    async def test_parse_zip_file(self, zip_parser, sample_zip):
        """Test parsing an actual ZIP file."""
        result = await zip_parser.parse(sample_zip)

        assert result.source_format == "zip"
        assert result.parser_name == "ZipParser"

    @pytest.mark.asyncio
    async def test_parse_content_delegates_to_markdown(self, zip_parser):
        """Test that parse_content delegates to MarkdownParser."""
        content = "# ZIP Archive: test.zip\n\nSome content"
        result = await zip_parser.parse_content(content)

        assert result.source_format == "zip"
        assert result.parser_name == "ZipParser"


# -----------------------------------------------------------------------------
# Audio Parser Tests
# -----------------------------------------------------------------------------


class TestAudioParser:
    """Test Audio file parser."""

    @pytest.fixture
    def audio_parser(self):
        return AudioParser()

    def test_supported_extensions(self, audio_parser):
        supported = audio_parser.supported_extensions
        assert ".mp3" in supported
        assert ".wav" in supported
        assert ".m4a" in supported
        assert ".ogg" in supported
        assert ".flac" in supported

    def test_format_size(self, audio_parser):
        """Test file size formatting."""
        assert audio_parser._format_size(500) == "500.0 B"
        assert audio_parser._format_size(2048) == "2.0 KB"
        assert audio_parser._format_size(2097152) == "2.0 MB"

    def test_format_duration(self, audio_parser):
        """Test duration formatting."""
        assert audio_parser._format_duration(65) == "1:05"
        assert audio_parser._format_duration(3661) == "1:01:01"
        assert audio_parser._format_duration(45) == "0:45"

    @pytest.mark.asyncio
    async def test_parse_content_delegates_to_markdown(self, audio_parser):
        """Test that parse_content delegates to MarkdownParser."""
        content = "# Audio File: test.mp3\n\n**Duration:** 3:45"
        result = await audio_parser.parse_content(content, source_path="test.mp3")

        assert result.source_format == "audio"
        assert result.parser_name == "AudioParser"


# -----------------------------------------------------------------------------
# Registry Integration Tests
# -----------------------------------------------------------------------------


class TestRegistryIntegration:
    """Test parser registration in registry."""

    @pytest.fixture
    def registry(self):
        from openviking.parse.registry import ParserRegistry

        return ParserRegistry(register_optional=False)

    def test_markitdown_parsers_registered(self, registry):
        """Test all markitdown parsers are registered by default."""
        parsers = registry.list_parsers()
        assert "word" in parsers
        assert "powerpoint" in parsers
        assert "excel" in parsers
        assert "epub" in parsers
        assert "zip" in parsers
        assert "audio" in parsers

    def test_extensions_mapped(self, registry):
        """Test file extensions are properly mapped."""
        extensions = registry.list_supported_extensions()
        assert ".docx" in extensions
        assert ".pptx" in extensions
        assert ".xlsx" in extensions
        assert ".xlsm" in extensions
        assert ".epub" in extensions
        assert ".zip" in extensions
        assert ".mp3" in extensions
        assert ".wav" in extensions

    def test_get_parser_for_file(self, registry):
        """Test getting parser for specific file types."""
        assert registry.get_parser_for_file("test.docx") is not None
        assert registry.get_parser_for_file("test.xlsx") is not None
        assert registry.get_parser_for_file("test.zip") is not None
        assert registry.get_parser_for_file("test.pptx") is not None
        assert registry.get_parser_for_file("test.epub") is not None
        assert registry.get_parser_for_file("test.mp3") is not None

    def test_parser_types(self, registry):
        """Test parsers are the correct types."""
        assert isinstance(registry.get_parser("word"), WordParser)
        assert isinstance(registry.get_parser("powerpoint"), PowerPointParser)
        assert isinstance(registry.get_parser("excel"), ExcelParser)
        assert isinstance(registry.get_parser("epub"), EPubParser)
        assert isinstance(registry.get_parser("zip"), ZipParser)
        assert isinstance(registry.get_parser("audio"), AudioParser)
