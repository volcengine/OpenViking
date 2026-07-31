# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for parsed documents whose Markdown body must remain in one file."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.parse.mode import ParseMode, normalize_parse_mode
from openviking.parse.parsers.markdown import MarkdownParser
from openviking.parse.parsers.pdf import PDFParser
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.config.parser_config import ParserConfig, PDFConfig


class _FakeVikingFS:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.directories: set[str] = set()

    def create_temp_uri(self) -> str:
        return "viking://temp/pdf-no-split"

    async def mkdir(self, uri: str, exist_ok: bool = False) -> None:
        del exist_ok
        self.directories.add(uri)

    async def write_file(self, uri: str, content: str) -> None:
        self.files[uri] = content


def _long_markdown(*, with_headings: bool = False) -> str:
    sections = []
    for index in range(30):
        heading = f"## Scene {index}\n\n" if with_headings else ""
        sections.append(f"{heading}paragraph {index} " + "x" * 500)
    return "\n\n".join(sections)


def test_normalize_parse_mode_accepts_no_split_and_rejects_no_parse() -> None:
    assert normalize_parse_mode("default") is ParseMode.DEFAULT
    assert normalize_parse_mode("no_split") is ParseMode.NO_SPLIT
    assert normalize_parse_mode(ParseMode.NO_SPLIT) is ParseMode.NO_SPLIT

    with pytest.raises(InvalidArgumentError, match="default, no_split"):
        normalize_parse_mode("no_parse")


@pytest.mark.asyncio
@pytest.mark.parametrize("with_headings", [False, True])
async def test_long_markdown_no_split_plans_one_complete_file(
    with_headings: bool,
) -> None:
    content = _long_markdown(with_headings=with_headings)
    parser = MarkdownParser(
        config=ParserConfig(max_section_size=32, max_section_chars=128)
    )

    layout = await parser._compute_layout(
        content,
        "viking://temp/test",
        source_path="/tmp/社交网络中英文剧本.pdf",
        resource_name="社交网络中英文剧本",
        split_content=False,
    )

    writes = [op for op in layout.ops if op.kind == "write"]
    assert [(op.uri, op.content) for op in writes] == [
        (
            "viking://temp/test/社交网络中英文剧本/社交网络中英文剧本.md",
            content,
        )
    ]


@pytest.mark.asyncio
async def test_long_markdown_default_mode_still_splits() -> None:
    content = _long_markdown()
    parser = MarkdownParser(
        config=ParserConfig(max_section_size=32, max_section_chars=128)
    )

    layout = await parser._compute_layout(
        content,
        "viking://temp/test",
        source_path="/tmp/社交网络中英文剧本.pdf",
        resource_name="社交网络中英文剧本",
    )

    writes = [op for op in layout.ops if op.kind == "write"]
    assert len(writes) > 1


@pytest.mark.asyncio
async def test_pdf_no_split_converts_to_one_complete_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "社交网络中英文剧本.pdf"
    source.write_bytes(b"%PDF-fixture")
    content = _long_markdown(with_headings=True)
    fake_fs = _FakeVikingFS()
    parser = PDFParser(
        PDFConfig(strategy="local", max_section_size=32, max_section_chars=128)
    )
    markdown_parser = parser._get_markdown_parser()
    monkeypatch.setattr(markdown_parser, "_get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        parser,
        "_convert_to_markdown",
        AsyncMock(return_value=(content, {})),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.storage.get_storage",
        lambda: SimpleNamespace(media_dir=tmp_path),
    )

    result = await parser.parse(
        source,
        resource_name="社交网络中英文剧本",
        split_content=False,
    )

    assert result.parser_name == "PDFParser"
    assert fake_fs.files == {
        (
            "viking://temp/pdf-no-split/社交网络中英文剧本/"
            "社交网络中英文剧本.md"
        ): content
    }
    assert not any(uri.endswith(".pdf") for uri in fake_fs.files)
