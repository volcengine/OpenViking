# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OpenAI VLM backend implementation"""

import asyncio
import base64
from pathlib import Path
from typing import Any, Dict, List, Union

from ..base import VLMBase


class OpenAIVLM(VLMBase):
    """OpenAI VLM backend"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._sync_client = None
        self._async_client = None
        # Ensure provider type is correct
        self.provider = "openai"

    def get_client(self):
        """Get sync client"""
        if self._sync_client is None:
            try:
                import openai
            except ImportError:
                raise ImportError("Please install openai: pip install openai")
            self._sync_client = openai.OpenAI(api_key=self.api_key, base_url=self.api_base)
        return self._sync_client

    def get_async_client(self):
        """Get async client"""
        if self._async_client is None:
            try:
                import openai
            except ImportError:
                raise ImportError("Please install openai: pip install openai")
            self._async_client = openai.AsyncOpenAI(api_key=self.api_key, base_url=self.api_base)
        return self._async_client

    def _update_token_usage_from_response(self, response):
        if hasattr(response, "usage") and response.usage:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            self.update_token_usage(
                model_name=self.model or "gpt-4o-mini",
                provider=self.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        return

    def get_completion(self, prompt: str, thinking: bool = False) -> str:
        """Get text completion"""
        client = self.get_client()
        kwargs = {
            "model": self.model or "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
        }

        response = client.chat.completions.create(**kwargs)
        self._update_token_usage_from_response(response)
        return response.choices[0].message.content or ""

    async def get_completion_async(
        self, prompt: str, thinking: bool = False, max_retries: int = 0
    ) -> str:
        """Get text completion asynchronously"""
        client = self.get_async_client()
        kwargs = {
            "model": self.model or "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
        }

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                response = await client.chat.completions.create(**kwargs)
                self._update_token_usage_from_response(response)
                return response.choices[0].message.content or ""
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)

        if last_error:
            raise last_error
        else:
            raise RuntimeError("Unknown error in async completion")

    def _prepare_image(self, image: Union[str, Path, bytes]) -> Dict[str, Any]:
        """Prepare image data"""
        if isinstance(image, bytes):
            b64 = base64.b64encode(image).decode("utf-8")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        elif isinstance(image, Path) or (
            isinstance(image, str) and not image.startswith(("http://", "https://"))
        ):
            path = Path(image)
            suffix = path.suffix.lower()
            mime_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(suffix, "image/png")
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            }
        else:
            return {"type": "image_url", "image_url": {"url": image}}

    def get_vision_completion(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
        thinking: bool = False,
    ) -> str:
        """Get vision completion"""
        client = self.get_client()

        content = []
        for img in images:
            content.append(self._prepare_image(img))
        content.append({"type": "text", "text": prompt})

        kwargs = {
            "model": self.model or "gpt-4o-mini",
            "messages": [{"role": "user", "content": content}],
            "temperature": self.temperature,
        }

        response = client.chat.completions.create(**kwargs)
        self._update_token_usage_from_response(response)
        return response.choices[0].message.content or ""

    async def get_vision_completion_async(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
        thinking: bool = False,
    ) -> str:
        """Get vision completion asynchronously"""
        client = self.get_async_client()

        content = []
        for img in images:
            content.append(self._prepare_image(img))
        content.append({"type": "text", "text": prompt})

        kwargs = {
            "model": self.model or "gpt-4o-mini",
            "messages": [{"role": "user", "content": content}],
            "temperature": self.temperature,
        }

        response = await client.chat.completions.create(**kwargs)
        self._update_token_usage_from_response(response)
        return response.choices[0].message.content or ""
