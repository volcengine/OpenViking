# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Heading exclusion must preserve overlapping fenced, comment and indent ranges."""

import random

import pytest

from openviking.parse.parsers.markdown import MarkdownParser


def _reference(parser, content):
    ranges = [
        (m.start(), m.end())
        for pattern in (
            parser._code_block_pattern,
            parser._html_comment_pattern,
            parser._indented_code_pattern,
        )
        for m in pattern.finditer(content)
    ]
    return [
        (m.start(), m.end(), m.group(2).strip(), len(m.group(1)))
        for m in parser._heading_pattern.finditer(content)
        if not any(start <= m.start() < end for start, end in ranges)
        and not (m.start() > 0 and content[m.start() - 1] == "\\")
    ]


def test_excluded_intervals_match_previous_rule_on_seeded_documents():
    parser = MarkdownParser()
    rng = random.Random(707)
    blocks = [
        "## Visible 中文\n",
        "<!--\n# hidden\n-->\n",
        "```python\n# fenced\n```\n",
        "    # indented\n",
        "\\# escaped\n",
        "ordinary 🙂\n",
        "# Head\n",
        "<!--\n```\n## overlapping\n```\n-->\n",
        "\n",
        "```\n<!--\n# nested\n-->\n```\n",
    ]
    for _ in range(1000):
        content = "".join(rng.choices(blocks, k=rng.randrange(1, 80)))
        assert parser._find_headings(content) == _reference(parser, content)


@pytest.mark.asyncio
async def test_layout_matches_reference_for_mixed_document(monkeypatch):
    parser = MarkdownParser()
    content = (
        "# Introduction\nbody\n<!--\n## hidden\n-->\n```\n# code\n```\n## Visible\nparagraph\n" * 20
    )
    actual = await parser._compute_layout(content, "viking://temp/test", resource_name="test")
    monkeypatch.setattr(parser, "_find_headings", lambda text: _reference(parser, text))
    expected = await parser._compute_layout(content, "viking://temp/test", resource_name="test")
    assert actual == expected
