# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""VLM (Vision-Language Model) module"""

from .backends.openai_vlm import OpenAIVLM
from .backends.volcengine_vlm import VolcEngineVLM
from .base import VLMBase, VLMFactory

__all__ = [
    "VLMBase",
    "VLMFactory",
    "OpenAIVLM",
    "VolcEngineVLM",
]
