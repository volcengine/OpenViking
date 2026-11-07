# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Oversized-paragraph splitting prefers sentence boundaries within the budget."""

from openviking.parse.parsers.markdown import MarkdownParser

DEMO_TEXT = (
    "The robot ordered a coffee. "
    "It then remembered it had no mouth. "
    "The waiter offered a charging cable. "
    "The robot left a five-star review."
)


def test_demo_chunks_are_complete_sentences():
    parser = MarkdownParser()
    parts = parser._smart_split_content(DEMO_TEXT, max_size=40)
    assert parts == [
        "The robot ordered a coffee. ",
        "It then remembered it had no mouth. ",
        "The waiter offered a charging cable. ",
        "The robot left a five-star review.",
    ]


def test_chunks_reconstruct_source_and_respect_budget():
    parser = MarkdownParser()
    parts = parser._smart_split_content(DEMO_TEXT, max_size=40)
    assert "".join(parts) == DEMO_TEXT
    assert all(len(part) <= 40 for part in parts)


def test_last_fitting_sentence_boundary_is_preferred():
    text = (
        "The alpha beta gamma. "  # ends at 22
        "Delta epsilon zeta! "  # ends at 42
        "Eta theta iota? "  # ends at 58
        "Kappa lambda mu nu."  # ends at 77
    )
    parser = MarkdownParser()
    parts = parser._smart_split_content(text, max_size=40)
    assert parts == [
        "The alpha beta gamma. ",
        "Delta epsilon zeta! Eta theta iota? ",
        "Kappa lambda mu nu.",
    ]
    assert all(len(part) <= 40 for part in parts)


def test_cjk_sentence_endings_split_without_trailing_space():
    parser = MarkdownParser()
    parts = parser._smart_split_content("第一句。第二句。第三句。", max_size=10)
    assert parts == ["第一句。第二句。", "第三句。"]


def test_whitespace_boundary_used_without_sentence_punctuation():
    text = ("word " * 60).rstrip() + " "  # 300 chars, no sentence punctuation
    parser = MarkdownParser()
    parts = parser._smart_split_content(text, max_size=40)
    assert "".join(parts) == text
    assert all(len(part) <= 40 for part in parts)
    assert all(part.endswith(" ") for part in parts[:-1])


def test_inline_code_punctuation_is_not_a_sentence_boundary():
    code = "`p. q. r. s. t. u. v. w.`"
    text = "Intro " + code + " then the actual sentence boundary arrives much later in the flow."
    parser = MarkdownParser()
    parts = parser._smart_split_content(text, max_size=40)
    assert "".join(parts) == text
    assert all(len(part) <= 40 for part in parts)
    # The fitting inline-code span is never split across chunks.
    assert sum(part.count(code) for part in parts) == 1


def test_symbol_run_without_endings_keeps_character_fallback():
    parser = MarkdownParser()
    paragraph = "中" * 5000
    parts = parser._smart_split_content(paragraph, max_size=40)
    assert len(parts) == 125
    assert all(len(part) == 40 for part in parts)
    assert "".join(parts) == paragraph
