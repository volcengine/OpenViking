# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Token counting and truncation helpers."""

import re
from functools import lru_cache

DEFAULT_TOKEN_ENCODING = "cl100k_base"
DEFAULT_TRUNCATION_SUFFIX = "\n...(truncated for embedding)"

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_NON_WHITESPACE_PATTERN = re.compile(r"[^\s]")


@lru_cache(maxsize=8)
def _get_tiktoken_encoding(encoding_name: str):
    import tiktoken

    return tiktoken.get_encoding(encoding_name)


def estimate_token_count(text: str) -> int:
    """Estimate tokens for multilingual text without provider-specific tokenizers."""
    if not text:
        return 0

    cjk_chars = len(_CJK_PATTERN.findall(text))
    other_chars = len(_NON_WHITESPACE_PATTERN.findall(text)) - cjk_chars
    return max(1, int(cjk_chars * 0.7 + other_chars * 0.3))


def count_tokens(text: str, encoding_name: str = DEFAULT_TOKEN_ENCODING) -> int:
    """Count tokens with tiktoken when available, otherwise use a multilingual estimate."""
    try:
        return len(_get_tiktoken_encoding(encoding_name).encode(text))
    except Exception:
        return estimate_token_count(text)


def truncate_text_by_tokens(
    text: str,
    max_tokens: int,
    *,
    encoding_name: str = DEFAULT_TOKEN_ENCODING,
    suffix: str = DEFAULT_TRUNCATION_SUFFIX,
) -> str:
    """Truncate text to a token budget with a best-effort tokenizer fallback."""
    if not text:
        return text

    if max_tokens <= 0:
        return suffix.lstrip()

    try:
        encoding = _get_tiktoken_encoding(encoding_name)
        token_ids = encoding.encode(text)
        if len(token_ids) <= max_tokens:
            return text
        return encoding.decode(token_ids[:max_tokens]) + suffix
    except Exception:
        estimated_tokens = estimate_token_count(text)
        if estimated_tokens <= max_tokens:
            return text

        char_budget = max(1, int(len(text) * max_tokens / max(estimated_tokens, 1)))
        return text[:char_budget].rstrip() + suffix
