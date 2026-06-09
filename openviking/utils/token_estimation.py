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
