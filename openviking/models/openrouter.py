# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenRouter app attribution (https://openrouter.ai/docs/app-attribution)."""

from typing import Dict, Optional
from urllib.parse import urlparse

OPENROUTER_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://openviking.ai",
    "X-OpenRouter-Title": "OpenViking",
}
_ATTRIBUTION_KEYS = {"http-referer", "x-openrouter-title", "x-title"}


def with_openrouter_attribution(
    headers: Optional[Dict[str, str]], api_base: Optional[str], model: Optional[str]
) -> Optional[Dict[str, str]]:
    """Add OpenViking attribution headers to OpenRouter requests.

    Requests to other providers, and requests where the user already set any
    attribution header, are returned unchanged.
    """
    host = (urlparse(api_base or "").hostname or "").lower()
    is_openrouter = (
        host == "openrouter.ai"
        or host.endswith(".openrouter.ai")
        or (model or "").lower().startswith("openrouter/")
    )
    if not is_openrouter or any(key.lower() in _ATTRIBUTION_KEYS for key in headers or {}):
        return headers
    return {**OPENROUTER_ATTRIBUTION_HEADERS, **(headers or {})}
