# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for bounding text sent to embedding providers."""

from __future__ import annotations

import re

EMBEDDING_TRUNCATION_SUFFIX = "\n...(truncated for embedding)"
_CJK_RUN = re.compile("[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")


def estimate_embedding_input_tokens(text: str) -> int:
    """Estimate CJK=1 and all other characters=1/4 tokens, rounding up."""
    other_chars = len(text) if text.isascii() else len(_CJK_RUN.sub("", text))
    return len(text) - other_chars + (other_chars + 3) // 4


def truncate_embedding_input(
    text: str,
    max_tokens: int,
    suffix: str = EMBEDDING_TRUNCATION_SUFFIX,
) -> str:
    """Keep the original estimated-token prefix budget, then append the marker."""
    if not text:
        return text
    if max_tokens <= 0:
        return suffix.lstrip()
    if len(text) <= max_tokens:
        return text

    # Quarter-token units preserve the original estimate without rounding each
    # run. No prefix longer than 4 * max_tokens can fit, even with only ASCII.
    max_units = max_tokens * 4
    if text.isascii():
        return text if len(text) <= max_units else text[:max_units].rstrip() + suffix
    scan_end = min(len(text), max_units + 1)
    units = position = 0
    for match in _CJK_RUN.finditer(text, 0, scan_end):
        start, end = match.span()
        # Ordinary characters preceding the CJK run cost one unit each.
        if units + start - position > max_units:
            prefix_end = position + max_units - units
            return text[:prefix_end].rstrip() + suffix
        units += start - position
        if units + (end - start) * 4 > max_units:
            prefix_end = start + (max_units - units) // 4
            return text[:prefix_end].rstrip() + suffix
        units += (end - start) * 4
        position = end

    if units + scan_end - position <= max_units:
        return text
    prefix_end = position + max_units - units
    return text[:prefix_end].rstrip() + suffix


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
