# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any, Dict

from .openai_vlm import OpenAIVLM

DEFAULT_KIMI_API_BASE = "https://api.kimi.com/coding"
DEFAULT_KIMI_MODEL = "kimi-code"
DEFAULT_KIMI_USER_AGENT = "KimiCLI/1.30.0"
KIMI_LEGACY_MODEL_ALIASES = {
    "kimi-code": "kimi-for-coding",
    "k2p5": "kimi-for-coding",
}


def _normalize_kimi_api_base(api_base: str | None) -> str:
    normalized = str(api_base or DEFAULT_KIMI_API_BASE).rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def normalize_kimi_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply Kimi defaults; the embedded gateway imports credentials through this too."""
    normalized = dict(config)
    model = str(normalized.get("model") or DEFAULT_KIMI_MODEL).strip() or DEFAULT_KIMI_MODEL
    normalized["provider"] = "kimi"
    normalized["api_base"] = _normalize_kimi_api_base(normalized.get("api_base"))
    normalized["model"] = KIMI_LEGACY_MODEL_ALIASES.get(model, model)
    extra_headers = dict(normalized.get("extra_headers") or {})
    if not any(key.lower() == "user-agent" for key in extra_headers):
        extra_headers["User-Agent"] = DEFAULT_KIMI_USER_AGENT
    normalized["extra_headers"] = extra_headers
    return normalized


class KimiVLM(OpenAIVLM):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(normalize_kimi_config(config))
