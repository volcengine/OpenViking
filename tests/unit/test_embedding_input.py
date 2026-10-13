# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Property tests for the embedding input token guard (issue #5895).

The estimator is consumed as an upper bound by the truncation guard, so these
tests pin invariants rather than tokenizer constants:

- CJK characters carry headroom over the 1 token/char floor (measured
  1.4-1.7 tokens/char on CJK-heavy byte-level tokenizers);
- non-ASCII, non-CJK characters cost at least 1 token each instead of
  falling into the cheap ``other/4`` bucket;
- ``estimate(truncate(text, n)) <= n`` — the truncation suffix is counted
  inside the budget it is appended to.

Pure functions: no network, no provider key.
"""

from __future__ import annotations

import math

from openviking.utils.embedding_input import (
    DEFAULT_CJK_TOKENS_PER_CHAR,
    EMBEDDING_TRUNCATION_SUFFIX,
    estimate_embedding_input_tokens,
    truncate_embedding_input,
)

CJK_PROBE_CHARS = (
    "\u4e2d",  # CJK Unified Ideographs: 中
    "\u3042",  # Hiragana: あ
    "\uac00",  # Hangul syllable: 가
    "\u1100",  # Hangul Jamo: ᄀ
    "\u3400",  # CJK Extension A
    "\u3001",  # CJK punctuation: 、
    "\uff2c",  # fullwidth form: Ｌ
    "\U00020000",  # CJK Extension B (astral plane)
    "\U0002ebf0",  # CJK Extension I (astral plane)
    "\U00030000",  # CJK Extension G (astral plane)
    "\U00031350",  # CJK Extension H (astral plane)
)
NON_CJK_NON_ASCII_CHARS = (
    "\U0001f44d",  # emoji: 👍 (used to estimate ceil(1/4) via the other/4 bucket)
    "\u041f",  # Cyrillic: П
    "\u00e9",  # Latin-1 supplement: é
)


def test_empty_text_estimates_zero():
    assert estimate_embedding_input_tokens("") == 0


def test_ascii_estimate_unchanged():
    assert estimate_embedding_input_tokens("a") == 1
    assert estimate_embedding_input_tokens("abcd" * 10) == 10


def test_cjk_priced_with_headroom_over_floor():
    for char in CJK_PROBE_CHARS:
        assert estimate_embedding_input_tokens(char) > 1
    assert estimate_embedding_input_tokens("中" * 100) == math.ceil(
        100 * DEFAULT_CJK_TOKENS_PER_CHAR
    )


def test_non_ascii_non_cjk_costs_at_least_one_token_per_char():
    for char in NON_CJK_NON_ASCII_CHARS:
        text = char * 100
        assert estimate_embedding_input_tokens(text) >= len(text)


class TestTruncateStaysWithinBound:
    text_cjk = "中文" * 200
    text_mixed = "汉字与 emoji 👍 混排 plus ascii prose " * 300

    def test_cjk_text(self):
        for max_tokens in (10, 100, 800):
            result = truncate_embedding_input(self.text_cjk, max_tokens)
            assert estimate_embedding_input_tokens(result) <= max_tokens

    def test_mixed_script_text(self):
        for max_tokens in (12, 200, 5000):
            result = truncate_embedding_input(self.text_mixed, max_tokens)
            assert estimate_embedding_input_tokens(result) <= max_tokens

    def test_suffix_is_counted_inside_the_budget(self):
        result = truncate_embedding_input(self.text_cjk, 10)
        assert result.endswith("...(truncated for embedding)")
        assert estimate_embedding_input_tokens(result) <= 10

    def test_text_within_bound_returned_unchanged(self):
        text = "hello world"
        assert truncate_embedding_input(text, 100) == text

    def test_budget_smaller_than_suffix_returns_suffix_only(self):
        result = truncate_embedding_input("x" * 1000, 2)
        assert result == EMBEDDING_TRUNCATION_SUFFIX.lstrip()

    def test_non_positive_budget_returns_suffix_only(self):
        assert truncate_embedding_input("x" * 10, 0) == EMBEDDING_TRUNCATION_SUFFIX.lstrip()
