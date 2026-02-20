# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""LiteLLM VLM Provider implementation with multi-provider support."""

import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import asyncio
import base64
from pathlib import Path
from typing import Any, Dict, List, Union

import litellm
from litellm import acompletion, completion

from ..base import VLMBase
from ..registry import find_by_model, find_gateway


class LiteLLMVLMProvider(VLMBase):
    """
    Multi-provider VLM implementation based on LiteLLM.

    Supports OpenRouter, Anthropic, OpenAI, Gemini, DeepSeek, VolcEngine and many other providers
    through a unified interface. Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self._provider_name = config.get("provider")
        self._extra_headers = config.get("extra_headers") or {}
        self._thinking = config.get("thinking", False)

        self._gateway = find_gateway(self._provider_name, self.api_key, self.api_base)

        if self.api_key:
            self._setup_env(self.api_key, self.api_base, self.model)

        if self.api_base:
            litellm.api_base = self.api_base

        litellm.suppress_debug_info = True
        litellm.drop_params = True

    def _setup_env(self, api_key: str, api_base: str | None, model: str | None) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model or "")
        if not spec:
            return

        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base or "")
            os.environ.setdefault(env_name, resolved)

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            prefix = self._gateway.litellm_prefix
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        if self._provider_name == "openai" and self.api_base:
            from openviking.models.vlm.registry import find_by_name
            openai_spec = find_by_name("openai")
            is_openai_official = "api.openai.com" in self.api_base
            if openai_spec and not is_openai_official and not model.startswith("openai/"):
                return f"openai/{model}"

        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"
        return model

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return

        if self._provider_name == "volcengine":
            kwargs["thinking"] = {"type": "enabled" if self._thinking else "disabled"}

    def _prepare_image(self, image: Union[str, Path, bytes]) -> Dict[str, Any]:
        """Prepare image data for vision completion."""
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

    def _build_kwargs(self, model: str, messages: list) -> dict[str, Any]:
        """Build kwargs for LiteLLM call."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
        }

        self._apply_model_overrides(model, kwargs)

        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self._extra_headers:
            kwargs["extra_headers"] = self._extra_headers

        return kwargs

    def get_completion(self, prompt: str, thinking: bool = False) -> str:
        """Get text completion synchronously."""
        model = self._resolve_model(self.model or "gpt-4o-mini")
        messages = [{"role": "user", "content": prompt}]
        original_thinking = self._thinking
        if thinking:
            self._thinking = thinking
        kwargs = self._build_kwargs(model, messages)
        self._thinking = original_thinking

        response = completion(**kwargs)
        self._update_token_usage_from_response(response)
        return response.choices[0].message.content or ""

    async def get_completion_async(
        self, prompt: str, thinking: bool = False, max_retries: int = 0
    ) -> str:
        """Get text completion asynchronously."""
        model = self._resolve_model(self.model or "gpt-4o-mini")
        messages = [{"role": "user", "content": prompt}]
        original_thinking = self._thinking
        if thinking:
            self._thinking = thinking
        kwargs = self._build_kwargs(model, messages)
        self._thinking = original_thinking

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                response = await acompletion(**kwargs)
                self._update_token_usage_from_response(response)
                return response.choices[0].message.content or ""
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)

        if last_error:
            raise last_error
        raise RuntimeError("Unknown error in async completion")

    def get_vision_completion(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
        thinking: bool = False,
    ) -> str:
        """Get vision completion synchronously."""
        model = self._resolve_model(self.model or "gpt-4o-mini")

        content = []
        for img in images:
            content.append(self._prepare_image(img))
        content.append({"type": "text", "text": prompt})

        messages = [{"role": "user", "content": content}]
        original_thinking = self._thinking
        if thinking:
            self._thinking = thinking
        kwargs = self._build_kwargs(model, messages)
        self._thinking = original_thinking

        response = completion(**kwargs)
        self._update_token_usage_from_response(response)
        return response.choices[0].message.content or ""

    async def get_vision_completion_async(
        self,
        prompt: str,
        images: List[Union[str, Path, bytes]],
        thinking: bool = False,
    ) -> str:
        """Get vision completion asynchronously."""
        model = self._resolve_model(self.model or "gpt-4o-mini")

        content = []
        for img in images:
            content.append(self._prepare_image(img))
        content.append({"type": "text", "text": prompt})

        messages = [{"role": "user", "content": content}]
        original_thinking = self._thinking
        if thinking:
            self._thinking = thinking
        kwargs = self._build_kwargs(model, messages)
        self._thinking = original_thinking

        response = await acompletion(**kwargs)
        self._update_token_usage_from_response(response)
        return response.choices[0].message.content or ""

    def _update_token_usage_from_response(self, response) -> None:
        """Update token usage from response."""
        if hasattr(response, "usage") and response.usage:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            self.update_token_usage(
                model_name=self.model or "unknown",
                provider=self.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
