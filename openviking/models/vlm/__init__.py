# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VLM (Vision-Language Model) module"""

from .base import VLMBase, VLMFactory
from .registry import get_all_provider_names, is_valid_provider

try:
    from .backends.litellm_vlm import LiteLLMVLMProvider
except ImportError:
    LiteLLMVLMProvider = None

try:
    from .backends.codex_vlm import CodexVLM
except ImportError:
    CodexVLM = None

try:
    from .backends.openai_vlm import OpenAIVLM
except ImportError:
    OpenAIVLM = None

try:
    from .backends.volcengine_vlm import VolcEngineVLM
except ImportError:
    VolcEngineVLM = None

__all__ = [
    "VLMBase",
    "VLMFactory",
    "OpenAIVLM",
    "CodexVLM",
    "VolcEngineVLM",
    "LiteLLMVLMProvider",
    "get_all_provider_names",
    "is_valid_provider",
]
