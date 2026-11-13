# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for bounding text sent to embedding providers."""

from __future__ import annotations

import re

EMBEDDING_TRUNCATION_SUFFIX = "\n...(truncated for embedding)"
_CJK_RUN = re.compile("[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
_CYRILLIC_RUN = re.compile("[\u0400-\u052f]+")
_SCRIPT_RUN = re.compile(f"({_CJK_RUN.pattern})|({_CYRILLIC_RUN.pattern})")

# Costs are tracked in tenths of a token so every per-character rate stays an
# exact integer. Tokenizers of the embedding models commonly served behind
# OpenAI-compatible endpoints spend far more than four characters per token on
# non-English text: measured against llama-server's /tokenize, Cyrillic prose
# needs roughly 2.7 characters per token and even English prose and shell code
# land near 3. A flat "4 characters per token" estimate therefore lets text
# truncated to ``max_input_tokens`` arrive with 30-50% more real tokens and get
# rejected by the provider. The rates below sit above those observed costs, so
# a document truncated under this estimate stays within the real limit.
_TENTHS_PER_TOKEN = 10
_CJK_TENTHS = 10  # CJK: one token per character.
_CYRILLIC_TENTHS = 5  # Cyrillic: two characters per token.
_ORDINARY_TENTHS = 4  # Everything else: 2.5 characters per token.
_MAX_TENTHS_PER_CHAR = _CJK_TENTHS


def _run_tenths(match: re.Match[str]) -> int:
    """Return the per-character cost of a script run matched by ``_SCRIPT_RUN``."""
    return _CJK_TENTHS if match.group(1) is not None else _CYRILLIC_TENTHS


def truncate_embedding_input(
    text: str,
    max_tokens: int,
    suffix: str = EMBEDDING_TRUNCATION_SUFFIX,
) -> str:
    """Keep the longest prefix whose estimated tokens plus ``suffix`` fit the budget."""
    if not text:
        return text
    if max_tokens <= 0:
        return suffix.lstrip()

    # Reserve room for the marker so the final payload still fits the budget.
    suffix_units = len(suffix) * _ORDINARY_TENTHS
    max_units = max(max_tokens * _TENTHS_PER_TOKEN - suffix_units, 0)
    if len(text) * _MAX_TENTHS_PER_CHAR <= max_units:
        return text
    if text.isascii():
        # ASCII is charged exactly _ORDINARY_TENTHS per character.
        return (
            text
            if len(text) * _ORDINARY_TENTHS <= max_units
            else text[: max_units // _ORDINARY_TENTHS].rstrip() + suffix
        )

    units = position = 0
    for match in _SCRIPT_RUN.finditer(text):
        start, end = match.span()
        # Ordinary characters preceding the script run cost 0.4 token each.
        ordinary_span = start - position
        if units + ordinary_span * _ORDINARY_TENTHS > max_units:
            prefix_end = position + (max_units - units) // _ORDINARY_TENTHS
            return text[:prefix_end].rstrip() + suffix
        units += ordinary_span * _ORDINARY_TENTHS
        per_char = _run_tenths(match)
        if units + (end - start) * per_char > max_units:
            prefix_end = start + (max_units - units) // per_char
            return text[:prefix_end].rstrip() + suffix
        units += (end - start) * per_char
        position = end

    if units + (len(text) - position) * _ORDINARY_TENTHS <= max_units:
        return text
    prefix_end = position + (max_units - units) // _ORDINARY_TENTHS
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
