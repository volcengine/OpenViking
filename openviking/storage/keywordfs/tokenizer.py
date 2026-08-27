# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Text tokenization for the local keyword sidecar.

FTS5's built-in ``unicode61`` tokenizer splits on whitespace and punctuation
but treats a run of CJK characters as a single token, which makes Chinese /
Japanese / Korean keyword recall unreliable. This module pre-tokenizes text
before it is stored so that ``unicode61`` can index and match the intended
terms:

- Latin / digits are split into words (``[a-zA-Z0-9_]+``), lowercased.
- CJK text is split per character (high recall) or into overlapping bigrams
  (higher precision for short queries). When the optional ``jieba`` dependency
  is installed and ``mode="auto"``, jieba word segmentation is used instead.
"""

from __future__ import annotations

import re
from typing import List, Optional

# CJK Unified Ideographs + Extension A + Compatibility Ideographs
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_NON_CJK_RUN_RE = re.compile(r"[^\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def has_cjk(text: str) -> bool:
    """Return True when the text contains CJK characters."""
    return bool(_CJK_RE.search(text or ""))


def _jieba_available() -> bool:
    try:
        import jieba  # noqa: F401

        return True
    except ImportError:
        return False


def _tokenize_cjk(seg: str, cjk_mode: str) -> List[str]:
    """Tokenize a run of CJK characters."""
    chars = list(seg)
    if cjk_mode == "bigram":
        tokens = list(chars)
        for i in range(len(chars) - 1):
            tokens.append(chars[i] + chars[i + 1])
        return tokens
    # char mode (default): one token per character
    return chars


def _tokenize_with_jieba(text: str) -> List[str]:
    """Word-segment CJK text with jieba (optional dependency)."""
    import jieba

    segs = jieba.cut_for_search(text)
    return [s for s in segs if s.strip()]


def tokenize(
    text: str,
    tokenizer_mode: str = "auto",
    cjk_mode: str = "char",
) -> str:
    """Normalize text into space-separated tokens for the FTS5 index/query.

    Args:
        text: Raw text to tokenize.
        tokenizer_mode: 'auto' | 'char' | 'jieba'.
        cjk_mode: 'char' | 'bigram' (used only when jieba is not applied).

    Returns:
        Space-separated token string (empty string for empty input).
    """
    if not text:
        return ""
    text = text.strip()
    if not text:
        return ""

    use_jieba = tokenizer_mode == "jieba" or (
        tokenizer_mode == "auto" and has_cjk(text) and _jieba_available()
    )
    if use_jieba:
        tokens = _tokenize_with_jieba(text)
        return " ".join(tokens)

    tokens: List[str] = []
    # Walk the text by CJK / non-CJK runs.
    idx = 0
    while idx < len(text):
        match = _CJK_RE.search(text, idx)
        if match is None:
            for word in _WORD_RE.findall(text[idx:].lower()):
                tokens.append(word)
            break
        start = match.start()
        if start > idx:
            for word in _WORD_RE.findall(text[idx:start].lower()):
                tokens.append(word)
        # Find the end of the contiguous CJK run.
        end = start
        while end < len(text) and _CJK_RE.match(text[end]):
            end += 1
        tokens.extend(_tokenize_cjk(text[start:end], cjk_mode))
        idx = end
    return " ".join(tokens)


def terms(text: str, tokenizer_mode: str = "auto", cjk_mode: str = "char") -> List[str]:
    """Return the token list for a query (used to decide whether to run keyword recall)."""
    return [t for t in tokenize(text, tokenizer_mode, cjk_mode).split() if t]
