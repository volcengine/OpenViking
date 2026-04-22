# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""GLM Coding Plan VLM backend."""

from __future__ import annotations

from typing import Any, Dict

from .openai_vlm import OpenAIVLM

DEFAULT_GLM_API_BASE = "https://api.z.ai/api/coding/paas/v4"
DEFAULT_GLM_MODEL = "glm-4.6v"


class GLMVLM(OpenAIVLM):
    """First-class GLM backend with Coding Plan defaults."""

    def __init__(self, config: Dict[str, Any]):
        normalized = dict(config)
        normalized["provider"] = "glm"
        normalized.setdefault("model", DEFAULT_GLM_MODEL)
        normalized["api_base"] = str(normalized.get("api_base") or DEFAULT_GLM_API_BASE).rstrip("/")
        super().__init__(normalized)
