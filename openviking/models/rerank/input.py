# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rerank content uses the same text/image parts as embedding queries."""

from typing import Any, Dict, List, Union

from openviking.utils.token_estimation import estimate_text_tokens, truncate_text_to_token_budget

RerankInput = Union[str, List[Dict[str, Any]]]


def text_content(content: RerankInput) -> str:
    if isinstance(content, str):
        return content
    return "".join(part["text"] for part in content if part.get("type") == "text")


def has_content(content: RerankInput) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    return any(
        part.get("type") == "image_url"
        or (part.get("type") == "text" and part.get("text", "").strip())
        for part in content
    )


def truncate_input(content: RerankInput, max_tokens: int) -> RerankInput:
    """Bound raw text without truncating image URLs or base64 payloads."""
    if isinstance(content, str):
        return truncate_text_to_token_budget(content, max_tokens)
    bounded = []
    for part in content:
        if part.get("type") == "text":
            text = truncate_text_to_token_budget(part["text"], max_tokens)
            max_tokens = max(0, max_tokens - estimate_text_tokens(text))
            bounded.append({**part, "text": text})
        else:
            bounded.append(part)
    return bounded
