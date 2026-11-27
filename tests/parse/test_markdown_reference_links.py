# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for external Markdown references across ingest sections."""

import pytest
from markdown_it import MarkdownIt

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.markdown import MarkdownParser
from openviking_cli.utils.config.parser_config import ParserConfig

DESTINATION = "https://example.com/help/install"
TITLE = "Setup guide"
DEFINITION = f'[setup]: {DESTINATION} "{TITLE}"'
PARAGRAPH = "This guide describes the ordinary steps and provides a worked example."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "reference", "expected_count"),
    [
        ("later-section", "Read [Setup][setup].", 1),
        ("later-section", "Read [Setup][SETUP] and also [setup][].", 2),
        ("earlier-section", "Read [Setup][setup].", 1),
        ("same-section", "Read [Setup][setup].", 1),
        ("unsplit", "Read [Setup][setup].", 1),
        ("inline", f'Read [Setup]({DESTINATION} "{TITLE}").', 1),
    ],
)
async def test_parse_preserves_external_reference_links(tmp_path, mode, reference, expected_count):
    prepare = f"# Prepare\n\n{reference}\n\n" + "\n".join([PARAGRAPH] * 9)
    install = "# Install\n\n" + "\n".join([PARAGRAPH] * 10)
    if mode == "same-section":
        prepare += f"\n\n{DEFINITION}"
    elif mode == "earlier-section":
        prepare, install = install + f"\n\n{DEFINITION}", prepare
    elif mode != "inline":
        install += f"\n\n{DEFINITION}"
    source = tmp_path / "guide.md"
    source.write_text(f"{prepare}\n\n{install}", encoding="utf-8")
    output = tmp_path / "parsed"
    max_chars = 900

    await MarkdownParser(config=ParserConfig(max_section_chars=max_chars)).parse(
        source,
        split_content=(mode != "unsplit"),
        parse_output_store=LocalParseOutputStore(str(output)),
    )

    artifacts = {
        part.stem: part.read_text(encoding="utf-8") for part in output.rglob("*.md")
    }
    if mode == "unsplit":
        assert len(artifacts) == 1
        [reference_artifact] = artifacts.values()
    else:
        assert set(artifacts) == {"Prepare", "Install"}
        assert all(len(part) <= max_chars for part in artifacts.values())
        reference_artifact = artifacts["Prepare"]
    links = [
        (child.attrGet("href"), child.attrGet("title"))
        for token in MarkdownIt("commonmark").parse(reference_artifact)
        for child in token.children or []
        if child.type == "link_open"
    ]
    assert links == [(DESTINATION, TITLE)] * expected_count
