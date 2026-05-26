# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for parser-produced attachments in MarkdownParser."""

import pytest

from openviking.parse.base import RESOURCE_ROOT_PLACEHOLDER, RESOURCE_ROOT_PLACEHOLDER_META_KEY
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig


class _FakeVikingFS:
    def __init__(self):
        self.dirs = []
        self.files = {}

    def create_temp_uri(self):
        return "viking://temp/attachments"

    async def mkdir(self, uri, exist_ok=False):
        if uri not in self.dirs:
            self.dirs.append(uri)

    async def write_file(self, uri, content):
        self.files[uri] = content.encode("utf-8") if isinstance(content, str) else content

    async def write_file_bytes(self, uri, content):
        self.files[uri] = content


@pytest.mark.asyncio
async def test_markdown_parser_persists_safe_parser_attachments(monkeypatch):
    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(BaseParser, "_get_viking_fs", lambda _self: fake_fs)
    parser = MarkdownParser(config=ParserConfig())

    result = await parser.parse_content(
        "hello",
        source_path="paper.pdf",
        resource_name="paper",
        attachments=[
            {"path": "../outside.png", "content": b"bad"},
            {"path": "/absolute.png", "content": b"bad"},
            {"path": "media/images/image-1.png", "content": b"image-bytes"},
        ],
    )

    assert result.temp_dir_path == "viking://temp/attachments"
    assert (
        fake_fs.files["viking://temp/attachments/paper/media/images/image-1.png"] == b"image-bytes"
    )
    assert fake_fs.files["viking://temp/attachments/paper/paper.md"] == b"hello"
    assert result.meta[RESOURCE_ROOT_PLACEHOLDER_META_KEY] == RESOURCE_ROOT_PLACEHOLDER
    assert all("outside" not in uri for uri in fake_fs.files)
    assert all("absolute" not in uri for uri in fake_fs.files)


@pytest.mark.asyncio
async def test_markdown_parser_copies_local_image_references(monkeypatch, tmp_path):
    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(BaseParser, "_get_viking_fs", lambda _self: fake_fs)
    parser = MarkdownParser(config=ParserConfig())

    image_path = tmp_path / "diagram.png"
    image_bytes = b"\x89PNG\r\n\x1a\nfake-png"
    image_path.write_bytes(image_bytes)
    markdown_path = tmp_path / "paper.md"
    markdown_path.write_text("intro\n\n![diagram](diagram.png)\n", encoding="utf-8")

    result = await parser.parse(markdown_path)

    markdown = fake_fs.files["viking://temp/attachments/paper/paper.md"].decode("utf-8")
    assert (
        markdown == f"intro\n\n![diagram]({RESOURCE_ROOT_PLACEHOLDER}/media/images/image-1.png)\n"
    )
    assert fake_fs.files["viking://temp/attachments/paper/media/images/image-1.png"] == image_bytes
    assert result.meta[RESOURCE_ROOT_PLACEHOLDER_META_KEY] == RESOURCE_ROOT_PLACEHOLDER
