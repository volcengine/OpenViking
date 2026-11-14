# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests that truncated embedding input stays within the character bound."""

import pytest

from openviking.utils.embedding_input import (
    EMBEDDING_TRUNCATION_SUFFIX,
    truncate_embedding_input,
)


@pytest.mark.parametrize(
    "text",
    [
        "a" * 200_000,
        "文" * 200_000,
        "x" * 50_000 + "文" * 200_000,
        "x" * 200_000 + "文" * 50_000,
    ],
)
def test_truncated_output_never_exceeds_char_bound(text):
    max_tokens = 32_768
    out = truncate_embedding_input(text, max_tokens)
    assert len(out) <= max_tokens * 4
    assert out.endswith(EMBEDDING_TRUNCATION_SUFFIX)


def test_short_ascii_input_is_unchanged():
    assert truncate_embedding_input("hello", 32_768) == "hello"


def test_budget_smaller_than_suffix_cuts_without_marker():
    assert truncate_embedding_input("first text", 1) == "firs"
