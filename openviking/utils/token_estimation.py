# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Shared conservative token estimation helpers."""

from __future__ import annotations

import math
from typing import Any


def _is_cjk_code_point(code_point: int) -> bool:
    return (
        0x3400 <= code_point <= 0x4DBF
        or 0x4E00 <= code_point <= 0x9FFF
        or 0xF900 <= code_point <= 0xFAFF
        or 0x20000 <= code_point <= 0x2EBEF
        or 0x3040 <= code_point <= 0x30FF
        or 0x31F0 <= code_point <= 0x31FF
        or 0xAC00 <= code_point <= 0xD7AF
        or 0x1100 <= code_point <= 0x11FF
        or 0x3130 <= code_point <= 0x318F
        or 0xFF00 <= code_point <= 0xFFEF
        or 0x3000 <= code_point <= 0x303F
    )


def _code_point_weight(code_point: int) -> float:
    if _is_cjk_code_point(code_point):
        return 1.5
    if code_point > 0xFFFF:
        return 2.0
    return 0.25


def estimate_text_tokens(text: str | None) -> int:
    """Estimate tokens with a CJK-aware fallback."""
    if not text:
        return 0
    return math.ceil(sum(_code_point_weight(ord(char)) for char in text))


def estimate_serialized_tokens(value: Any) -> int:
    """Estimate tokens for already-structured prompt-like values."""
    if value is None:
        return 0
    if isinstance(value, str):
        return estimate_text_tokens(value)
    return estimate_text_tokens(str(value))


def truncate_text_to_token_budget(
    text: str,
    max_tokens: int,
    *,
    marker: str = "\n…\n",
    head_ratio: float = 0.75,
) -> str:
    """Fit text to a conservative token budget while retaining its head and tail."""
    if not text or estimate_text_tokens(text) <= max_tokens:
        return text
    if max_tokens <= 0:
        return ""

    marker_tokens = estimate_text_tokens(marker)
    if marker_tokens >= max_tokens:
        marker = ""
        marker_tokens = 0

    content_tokens = max_tokens - marker_tokens
    bounded_head_ratio = min(max(head_ratio, 0.0), 1.0)
    head_tokens = int(content_tokens * bounded_head_ratio)
    tail_tokens = content_tokens - head_tokens

    def _prefix_length(value: str, budget: int) -> int:
        low = 0
        high = len(value)
        while low < high:
            mid = (low + high + 1) // 2
            if estimate_text_tokens(value[:mid]) <= budget:
                low = mid
            else:
                high = mid - 1
        return low

    head_end = _prefix_length(text, head_tokens)
    remaining = text[head_end:]
    tail_length = _prefix_length(remaining[::-1], tail_tokens)
    tail = remaining[len(remaining) - tail_length :] if tail_length else ""
    return text[:head_end].rstrip() + marker + tail.lstrip()
