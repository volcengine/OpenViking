# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""VolcEngine VLM backend implementation"""

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

from ..base import VLMBase
from .openai_vlm import OpenAIVLM

class VolcEngineVLM(OpenAIVLM):
    """VolcEngine VLM backend"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._sync_client = None
        self._async_client = None
        # Ensure provider type is correct
        self.provider = "volcengine"

        # VolcEngine-specific defaults
        if not self.api_base:
            self.api_base = "https://ark.cn-beijing.volces.com/api/v3"
        if not self.model:
            self.model = "doubao-seed-1-8-251228"

    def get_client(self):
        """Get sync client"""
        if self._sync_client is None:
            try:
                import volcenginesdkarkruntime
            except ImportError:
                raise ImportError(
                    "Please install volcenginesdkarkruntime: pip install volcenginesdkarkruntime"
                )
            self._sync_client = volcenginesdkarkruntime.Ark(
                api_key=self.api_key,
                base_url=self.api_base,
            )
        return self._sync_client

    def get_async_client(self):
        """Get async client"""
        if self._async_client is None:
            try:
                import volcenginesdkarkruntime
            except ImportError:
                raise ImportError(
                    "Please install volcenginesdkarkruntime: pip install volcenginesdkarkruntime"
                )
            self._async_client = volcenginesdkarkruntime.AsyncArk(
                api_key=self.api_key,
                base_url=self.api_base,
            )
        return self._async_client

    def get_completion(self, prompt: str) -> str:
        return super().get_completion(prompt)

    async def get_completion_async(self, prompt: str, max_retries: int = 0) -> str:
        return await super().get_completion_async(prompt, max_retries)

    def get_vision_completion(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
    ) -> str:
        return super().get_vision_completion(prompt, images)

    async def get_vision_completion_async(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
    ) -> str:
        return await super().get_vision_completion_async(prompt, images)
        
