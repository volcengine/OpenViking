# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Token counting and truncation helpers."""

import math
import re

DEFAULT_TRUNCATION_SUFFIX = "\n...(truncated for embedding)"

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def estimate_token_count(text: str) -> int:
    """Estimate tokens with a shared multilingual heuristic.

    This intentionally does not bind OpenViking to a provider-specific tokenizer.
    CJK characters are counted conservatively as one token each; other text uses
    the existing 4 chars/token approximation.
    """
    if not text:
        return 0

    cjk_chars = len(_CJK_PATTERN.findall(text))
    other_chars = len(text) - cjk_chars
    return max(1, cjk_chars + math.ceil(other_chars / 4))


def truncate_text_by_tokens(
    text: str,
    max_tokens: int,
    *,
    suffix: str = DEFAULT_TRUNCATION_SUFFIX,
) -> str:
    """Truncate text to an estimated token budget using the shared heuristic."""
    if not text:
        return text

    if max_tokens <= 0:
        return suffix.lstrip()

    estimated_tokens = estimate_token_count(text)
    if estimated_tokens <= max_tokens:
        return text

    low = 0
    high = len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_token_count(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid - 1

    return text[:low].rstrip() + suffix
