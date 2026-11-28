# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest
from markdown_it import MarkdownIt

from openviking.parse.parsers.anydoc_renderer import _AnyDocMarkdownRenderer


def _text(text, **style):
    return SimpleNamespace(
        kind="text", text=text, style=SimpleNamespace(**style) if style else None
    )


@pytest.mark.parametrize(
    ("inlines", "expected"),
    [
        ([_text("left"), _text("!", italic=True), _text("right")], "left<em>!</em>right"),
        ([_text("left"), _text("!", strike=True), _text("right")], "left<del>!</del>right"),
        (
            [_text("原价"), _text("（100元）", strike=True), _text("现价")],
            "原价<del>（100元）</del>现价",
        ),
        ([_text("a "), _text("old", strike=True), _text(" b")], "a <s>old</s> b"),
    ],
)
def test_styled_runs_keep_style_beside_surrounding_text(inlines, expected):
    document = SimpleNamespace(
        assets=[], blocks=[SimpleNamespace(kind="paragraph", content=inlines)], notes=[]
    )
    markdown = _AnyDocMarkdownRenderer(
        document, source_format="docx", resource_name="style", storage=SimpleNamespace()
    ).render()

    html = MarkdownIt("commonmark").enable("strikethrough").renderInline(markdown.strip())
    assert html == expected
