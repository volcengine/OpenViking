#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ZipParser single-root directory handling.

Verifies that ZipParser correctly handles:
1. ZIP with single top-level directory -> uses that directory name
2. ZIP with multiple top-level entries -> uses the extract directory
"""

import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.parsers.zip_parser import ZipParser


@pytest.mark.asyncio
async def test_zip_single_top_level_dir_uses_real_root(tmp_path: Path):
    zip_path = tmp_path / "tt_b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    parser = ZipParser()

    # Mock DirectoryParser.parse to capture what directory it's called with
    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="")

        # Verify DirectoryParser was called with the real root dir "tt_b"
        assert mock_dir_parse.called
        called_path = Path(mock_dir_parse.await_args.args[0])
        assert called_path.name == "tt_b"


@pytest.mark.asyncio
async def test_zip_single_top_level_dir_ignores_zip_source_name(tmp_path: Path):
    zip_path = tmp_path / "tt_b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    parser = ZipParser()

    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="", source_name="tt_b.zip")

        # Verify DirectoryParser was called with the real root dir "tt_b"
        called_path = Path(mock_dir_parse.await_args.args[0])
        assert called_path.name == "tt_b"
        # source_name should NOT be passed to DirectoryParser in this case
        assert "source_name" not in mock_dir_parse.await_args.kwargs


@pytest.mark.asyncio
async def test_zip_multiple_top_level_entries_keeps_extract_root(tmp_path: Path):
    zip_path = tmp_path / "mixed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a/readme.md", "# a\n")
        zf.writestr("b/readme.md", "# b\n")

    parser = ZipParser()

    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="")

        # Verify DirectoryParser was called with the extract dir, not "a" or "b"
        called_path = Path(mock_dir_parse.await_args.args[0])
        assert called_path.name != "a"
        assert called_path.name != "b"
        # Should have the ov_zip_ prefix
        assert called_path.name.startswith("ov_zip_")


@pytest.mark.asyncio
async def test_single_file_uses_source_name_for_resource_name(tmp_path: Path):
    """Test that source_name is passed through correctly when needed."""
    zip_path = tmp_path / "mixed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a/readme.md", "# a\n")
        zf.writestr("b/readme.md", "# b\n")

    parser = ZipParser()

    with patch("openviking.parse.parsers.directory.DirectoryParser.parse") as mock_dir_parse:
        mock_result = AsyncMock()
        mock_result.temp_dir_path = None
        mock_dir_parse.return_value = mock_result

        await parser.parse(zip_path, instruction="", source_name="aa.txt")

        # Verify source_name is passed when we use the extract root
        assert mock_dir_parse.await_args.kwargs.get("source_name") == "aa.txt"
