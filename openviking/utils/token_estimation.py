# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Shared conservative token estimation helpers."""

from __future__ import annotations

from typing import Any


def _build_token_unit_table() -> bytes:
    """Map each Unicode code point to its weight in quarter-token units."""
    units = bytearray(b"\x01") * 0x10000
    units.extend(b"\x08" * (0x110000 - 0x10000))
    for start, end in (
        (0x1100, 0x11FF),
        (0x3000, 0x30FF),
        (0x3130, 0x318F),
        (0x31F0, 0x31FF),
        (0x3400, 0x4DBF),
        (0x4E00, 0x9FFF),
        (0xAC00, 0xD7AF),
        (0xF900, 0xFAFF),
        (0xFF00, 0xFFEF),
        (0x20000, 0x2EBEF),
    ):
        units[start : end + 1] = b"\x06" * (end - start + 1)
    return bytes(units)


_TOKEN_UNIT_TABLE = _build_token_unit_table()


def estimate_text_tokens(text: str | None) -> int:
    """Estimate tokens with a CJK-aware fallback."""
    if not text:
        return 0
    if text.isascii():
        return (len(text) + 3) // 4

    # The translated string holds each character's numeric weight as a code point.
    units = sum(map(ord, text.translate(_TOKEN_UNIT_TABLE)))
    return (units + 3) // 4


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
