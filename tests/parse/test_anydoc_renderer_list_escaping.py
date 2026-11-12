"""Regression tests for literal numbered paragraphs in the AnyDoc renderer (#7174).

CommonMark turns a paragraph line that starts with ``1.`` / ``2)`` into an ordered list, so the
renderer must escape the list delimiter when the source paragraph text is not itself a list.
"""

import pytest

from openviking.parse.parsers.anydoc_renderer import _AnyDocMarkdownRenderer


def _escape(text: str, *, context: str = "block", at_line_start: bool = True) -> str:
    return _AnyDocMarkdownRenderer._escape_text(
        text,
        context=context,
        at_line_start=at_line_start,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1. literal paragraph", "1\\. literal paragraph"),
        ("2) literal paragraph", "2\\) literal paragraph"),
        ("10. multi digit prefix", "10\\. multi digit prefix"),
        ("2020. year at the start", "2020\\. year at the start"),
        ("123456789. nine digits", "123456789\\. nine digits"),
        ("1. Text after the prefix", "1\\. Text after the prefix"),
    ],
)
def test_numbered_paragraph_keeps_its_delimiter_literal(text: str, expected: str) -> None:
    assert _escape(text) == expected


def test_ordered_marker_after_a_line_break_is_escaped() -> None:
    assert _escape("line one\n1. literal") == "line one\n1\\. literal"


def test_mid_line_number_is_not_a_list_marker() -> None:
    assert _escape("value 1. stays inline") == "value 1. stays inline"


def test_marker_is_left_alone_when_not_at_line_start() -> None:
    assert _escape("1. literal", at_line_start=False) == "1. literal"


def test_marker_is_not_escaped_outside_block_contexts() -> None:
    assert _escape("1. literal", context="heading") == "1. literal"
    assert _escape("1. literal", context="table_cell") == "1. literal"


def test_more_than_nine_digits_is_not_a_list_marker() -> None:
    assert _escape("1234567890. too long a prefix") == "1234567890. too long a prefix"


def test_existing_block_markers_still_escape() -> None:
    assert _escape("- item") == "\\- item"
    assert _escape("* item") == "\\* item"
    assert _escape("# heading") == "\\# heading"
