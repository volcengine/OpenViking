#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openviking.utils.media_processor import UnifiedResourceProcessor


@pytest.mark.asyncio
async def test_zip_single_top_level_dir_uses_real_root(tmp_path: Path):
    zip_path = tmp_path / "tt_b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    processor = UnifiedResourceProcessor()
    processor._process_directory = AsyncMock(return_value="ok")

    result = await processor._process_file(zip_path, instruction="")

    assert result == "ok"
    called_dir = processor._process_directory.await_args.args[0]
    assert isinstance(called_dir, Path)
    assert called_dir.name == "tt_b"


@pytest.mark.asyncio
async def test_zip_single_top_level_dir_ignores_zip_source_name(tmp_path: Path):
    zip_path = tmp_path / "tt_b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("tt_b/bb/readme.md", "# hello\n")

    processor = UnifiedResourceProcessor()
    processor._process_directory = AsyncMock(return_value="ok")

    result = await processor._process_file(
        zip_path,
        instruction="",
        source_name="tt_b.zip",
    )

    assert result == "ok"
    called_dir = processor._process_directory.await_args.args[0]
    assert isinstance(called_dir, Path)
    assert called_dir.name == "tt_b"
    assert "source_name" not in processor._process_directory.await_args.kwargs


@pytest.mark.asyncio
async def test_zip_multiple_top_level_entries_keeps_extract_root(tmp_path: Path):
    zip_path = tmp_path / "mixed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a/readme.md", "# a\n")
        zf.writestr("b/readme.md", "# b\n")

    processor = UnifiedResourceProcessor()
    processor._process_directory = AsyncMock(return_value="ok")

    result = await processor._process_file(zip_path, instruction="")

    assert result == "ok"
    called_dir = processor._process_directory.await_args.args[0]
    assert isinstance(called_dir, Path)
    assert called_dir.name != "a"
    assert called_dir.name != "b"


@pytest.mark.asyncio
async def test_single_file_uses_source_name_for_resource_name(tmp_path: Path):
    file_path = tmp_path / "upload_123.txt"
    file_path.write_text("hello\n")

    processor = UnifiedResourceProcessor()

    with pytest.MonkeyPatch.context() as mp:
        parse_mock = AsyncMock(return_value="ok")
        mp.setattr("openviking.utils.media_processor.parse", parse_mock)

        result = await processor._process_file(
            file_path,
            instruction="",
            source_name="aa.txt",
        )

    assert result == "ok"
    assert parse_mock.await_args.kwargs["resource_name"] == "aa"
    assert parse_mock.await_args.kwargs["source_name"] == "aa.txt"
