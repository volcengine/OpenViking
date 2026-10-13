# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for bounding text sent to embedding providers."""

from __future__ import annotations

import math

EMBEDDING_TRUNCATION_SUFFIX = "\n...(truncated for embedding)"

# Headroom over the 1 token/char floor that CJK-heavy byte-level tokenizers
# actually charge (measured 1.4-1.7 tokens/char on Zhipu embedding-3-style
# providers). The estimate is consumed as an upper bound by the truncation
# guard, so the default rounds up to 2.0 instead of trusting the floor.
DEFAULT_CJK_TOKENS_PER_CHAR = 2.0

# Code point ranges priced at ``DEFAULT_CJK_TOKENS_PER_CHAR``: CJK ideographs
# (all published extension blocks), kana, hangul (jamo blocks included), CJK
# punctuation, and fullwidth forms. Unassigned gaps inside these planes are
# priced conservatively as well. Chars outside these ranges but above ASCII
# cost at least 1 token each.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3000, 0x303F),  # CJK Symbols and Punctuation
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xA960, 0xA97F),  # Hangul Jamo Extended-A
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0xD7B0, 0xD7FF),  # Hangul Jamo Extended-B
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),  # Halfwidth and Fullwidth Forms
    (0x1B000, 0x1B16F),  # Kana Supplement
    (0x20000, 0x2FA1F),  # Plane 2: CJK Ext B-F, I + Compat Ideographs Supp
    (0x30000, 0x323AF),  # CJK Extensions G-H
)


def _is_cjk(code_point: int) -> bool:
    return any(low <= code_point <= high for low, high in _CJK_RANGES)


def estimate_embedding_input_tokens(
    text: str,
    cjk_tokens_per_char: float | None = None,
) -> int:
    """Estimate tokens for the raw text embedding input guard.

    The result is a conservative upper bound per script class: CJK characters
    are priced at ``cjk_tokens_per_char`` (default
    ``DEFAULT_CJK_TOKENS_PER_CHAR``), other non-ASCII characters at >= 1 token
    each, and ASCII text at roughly 4 chars per token.
    """
    if not text:
        return 0
    ratio = DEFAULT_CJK_TOKENS_PER_CHAR if cjk_tokens_per_char is None else cjk_tokens_per_char
    cjk_chars = 0
    non_ascii_chars = 0
    ascii_chars = 0
    for char in text:
        code_point = ord(char)
        if code_point <= 0x7F:
            ascii_chars += 1
        elif _is_cjk(code_point):
            cjk_chars += 1
        else:
            non_ascii_chars += 1
    return max(1, math.ceil(cjk_chars * ratio) + non_ascii_chars + math.ceil(ascii_chars / 4))


def truncate_embedding_input(
    text: str,
    max_tokens: int,
    suffix: str = EMBEDDING_TRUNCATION_SUFFIX,
    cjk_tokens_per_char: float | None = None,
) -> str:
    """Trim raw text before embedding using the local estimate above.

    The suffix is counted inside ``max_tokens`` so the returned string still
    estimates within the bound it enforces. When the budget cannot fit the
    suffix, the suffix alone is returned, mirroring the ``max_tokens <= 0``
    precedent.
    """
    if not text:
        return text
    if max_tokens <= 0:
        return suffix.lstrip()
    if estimate_embedding_input_tokens(text, cjk_tokens_per_char) <= max_tokens:
        return text

    suffix_tokens = estimate_embedding_input_tokens(suffix, cjk_tokens_per_char)
    if suffix_tokens >= max_tokens:
        return suffix.lstrip()
    prefix_budget = max_tokens - suffix_tokens

    low = 0
    high = len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_embedding_input_tokens(text[:mid], cjk_tokens_per_char) <= prefix_budget:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + suffix


def resolve_embedding_max_input_tokens(
    config: dict[str, object] | None,
    default: int | None = None,
) -> int | None:
    """Read and normalize max_input_tokens from an embedder config dict."""
    raw_value = (config or {}).get("max_input_tokens", default)
    if raw_value is None:
        return default

    try:
        max_tokens = int(raw_value)
    except (TypeError, ValueError):
        return default

    if max_tokens <= 0:
        return default
    return max_tokens
