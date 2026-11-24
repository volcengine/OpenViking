# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""GLM Coding Plan VLM backend."""

from __future__ import annotations

from typing import Any, Dict

from .openai_vlm import OpenAIVLM

DEFAULT_GLM_API_BASE = "https://api.z.ai/api/coding/paas/v4"
DEFAULT_GLM_MODEL = "glm-4.6v"


def normalize_glm_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply GLM defaults; the embedded gateway imports credentials through this too."""
    normalized = dict(config)
    normalized["provider"] = "glm"
    normalized["model"] = normalized.get("model") or DEFAULT_GLM_MODEL
    normalized["api_base"] = str(normalized.get("api_base") or DEFAULT_GLM_API_BASE).rstrip("/")
    return normalized


class GLMVLM(OpenAIVLM):
    """First-class GLM backend with Coding Plan defaults."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(normalize_glm_config(config))
