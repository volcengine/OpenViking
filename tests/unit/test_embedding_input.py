# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for embedding input truncation bounds against real tokenizer costs.

The mock tokenizer below is calibrated against the measurements reported in
issue #6129 (llama-server ``POST /tokenize`` on the configured embedding
model): Cyrillic prose costs up to ~0.372 tokens per character (2.69
characters per token), and the worst non-Cyrillic sample — PowerShell source —
costs ~0.36 tokens per character, well above the historical "4 characters per
token" assumption. CJK characters are charged one token each.
"""

import math

from openviking.utils.embedding_input import (
    EMBEDDING_TRUNCATION_SUFFIX,
    resolve_embedding_max_input_tokens,
    truncate_embedding_input,
)

_MOCK_MILLI_PER_CHAR = {
    # thousandths of a token per character, from the issue #6129 measurements
    "cjk": 1000,  # one token per character
    "cyrillic": 372,  # measured 2.69 characters per token (worst Russian sample)
    "other": 360,  # measured 2.78 characters per token (worst non-Cyrillic sample)
}
_CYRILLIC = "\u0400"
_MAX_INPUT_TOKENS = 4096

RUSSIAN_MARKDOWN = "Русский текст для проверки памяти. " * 400
ENGLISH_MARKDOWN = "# Section\n\nSome plain English prose sentence here. " * 500
POWERSHELL_SOURCE = (
    'Get-ChildItem -Path $env:SystemRoot -Filter "*.log" | ForEach-Object { $_.Name }' * 250
)


def _mock_tokenizer_tokens(text: str) -> int:
    """Count tokens like the measured embedding-model tokenizer would."""
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff" or "\uac00" <= char <= "\ud7af")
    cyrillic = sum(1 for char in text if _CYRILLIC <= char <= "\u052f")
    other = len(text) - cjk - cyrillic
    total_milli = (
        cjk * _MOCK_MILLI_PER_CHAR["cjk"]
        + cyrillic * _MOCK_MILLI_PER_CHAR["cyrillic"]
        + other * _MOCK_MILLI_PER_CHAR["other"]
    )
    return math.ceil(total_milli / 1000)


def test_truncated_input_stays_within_real_token_budget():
    for sample in (RUSSIAN_MARKDOWN, ENGLISH_MARKDOWN, POWERSHELL_SOURCE):
        truncated = truncate_embedding_input(sample, _MAX_INPUT_TOKENS)
        assert truncated.endswith(EMBEDDING_TRUNCATION_SUFFIX)
        assert _mock_tokenizer_tokens(truncated) <= _MAX_INPUT_TOKENS


def test_truncation_respects_mixed_script_budget():
    # 10 Cyrillic + 10 CJK + 10 ASCII characters repeat: 0.5 + 1 + 0.4 = 1.9
    # tokens per repetition under the billing rates, so 2400 repetitions are
    # charged above a 4096-token budget and must be truncated.
    sample = "Ж中a" * 2400
    assert _mock_tokenizer_tokens(sample) > _MAX_INPUT_TOKENS
    truncated = truncate_embedding_input(sample, _MAX_INPUT_TOKENS)
    assert truncated.endswith(EMBEDDING_TRUNCATION_SUFFIX)
    assert _mock_tokenizer_tokens(truncated) <= _MAX_INPUT_TOKENS


def test_truncate_appends_marker_within_budget():
    text = "word " * 3000
    truncated = truncate_embedding_input(text, 100)
    assert truncated.endswith(EMBEDDING_TRUNCATION_SUFFIX)
    assert _mock_tokenizer_tokens(truncated) <= 100
    assert len(truncated) < len(text)


def test_truncate_passthrough_and_degenerate_budgets():
    text = "short"
    assert truncate_embedding_input(text, 100) == text
    assert truncate_embedding_input("", 100) == ""
    assert truncate_embedding_input(text, 0) == EMBEDDING_TRUNCATION_SUFFIX.lstrip()


def test_resolve_embedding_max_input_tokens():
    assert resolve_embedding_max_input_tokens(None) is None
    assert resolve_embedding_max_input_tokens({}, default=512) == 512
    assert resolve_embedding_max_input_tokens({"max_input_tokens": 2048}) == 2048
    assert resolve_embedding_max_input_tokens({"max_input_tokens": "2048"}) == 2048
    assert resolve_embedding_max_input_tokens({"max_input_tokens": 0}) is None
    assert resolve_embedding_max_input_tokens({"max_input_tokens": "bad"}) is None
