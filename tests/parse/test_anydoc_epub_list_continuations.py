# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest
from markdown_it import MarkdownIt

from openviking.parse.parsers.anydoc_renderer import _AnyDocMarkdownRenderer


def _paragraph(text: str):
    return SimpleNamespace(
        kind="paragraph",
        content=[SimpleNamespace(kind="text", text=text, style=None)],
    )


def _item(*blocks):
    return SimpleNamespace(marker_label=None, blocks=list(blocks))


def _list(marker: str, start: int, *items):
    return SimpleNamespace(
        kind="list",
        list=SimpleNamespace(marker=marker, start=start, items=list(items)),
    )


@pytest.mark.parametrize(
    ("marker", "start", "inline_label", "ordered"),
    [
        ("lower_roman", 2, "ii.", False),
        ("upper_roman", 2, "II.", False),
        ("lower_alpha", 27, "aa.", False),
        ("decimal", 12, "", True),
        ("bullet", 1, "", False),
    ],
)
def test_list_continuation_blocks_use_markdown_marker_width(marker, start, inline_label, ordered):
    """EPUB list labels remain visible without turning continuation blocks into code."""
    first_item = _item(
        _paragraph("First paragraph."),
        _paragraph("Second paragraph."),
        SimpleNamespace(kind="block_quote", blocks=[_paragraph("Quoted paragraph.")]),
        _list("bullet", 1, _item(_paragraph("Nested item."))),
    )
    next_item = _item(_paragraph("Next item."))
    document = SimpleNamespace(
        assets=[],
        notes=[],
        blocks=[_list(marker, start, first_item, next_item)],
    )
    renderer = _AnyDocMarkdownRenderer(
        document,
        source_format="epub",
        resource_name="lists",
        storage=SimpleNamespace(),
    )

    tokens = MarkdownIt("commonmark").parse(renderer.render())

    assert not any(token.type == "code_block" for token in tokens)
    outer_list = next(
        token
        for token in tokens
        if token.type in {"bullet_list_open", "ordered_list_open"} and token.level == 0
    )
    if ordered:
        assert outer_list.type == "ordered_list_open"
        assert outer_list.attrs["start"] == start
    else:
        assert outer_list.type == "bullet_list_open"
    first_item_start = next(
        index
        for index, token in enumerate(tokens)
        if token.type == "list_item_open" and token.level == 1
    )
    first_item_end = next(
        index
        for index in range(first_item_start + 1, len(tokens))
        if tokens[index].type == "list_item_close" and tokens[index].level == 1
    )
    first_item_tokens = tokens[first_item_start:first_item_end]

    first_paragraph = next(
        token.content
        for token in first_item_tokens
        if token.type == "inline" and token.level == 3
    )
    expected_first_paragraph = (
        f"{inline_label} First paragraph." if inline_label else "First paragraph."
    )
    assert first_paragraph == expected_first_paragraph
    assert sum(
        token.type == "paragraph_open" and token.level == 2 for token in first_item_tokens
    ) == 2
    assert any(
        token.type == "inline" and token.content == "Second paragraph."
        for token in first_item_tokens
    )
    assert any(
        token.type == "blockquote_open" and token.level == 2 for token in first_item_tokens
    )
    assert any(
        token.type == "inline" and token.content == "Quoted paragraph."
        for token in first_item_tokens
    )
    assert any(
        token.type == "bullet_list_open" and token.level == 2 for token in first_item_tokens
    )
    assert any(
        token.type == "inline" and token.content == "Nested item."
        for token in first_item_tokens
    )
