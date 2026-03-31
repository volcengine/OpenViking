# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LiteLLM VLM Provider implementation with multi-provider support."""

import json
import logging
import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import asyncio
import base64
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import litellm
from litellm import acompletion, completion

from ..base import ToolCall, VLMBase, VLMResponse

logger = logging.getLogger(__name__)

PROVIDER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "keywords": ("openrouter",),
        "env_key": "OPENROUTER_API_KEY",
        "litellm_prefix": "openrouter",
    },
    "hosted_vllm": {
        "keywords": ("hosted_vllm",),
        "env_key": "HOSTED_VLLM_API_KEY",
        "litellm_prefix": "hosted_vllm",
    },
    "ollama": {
        "keywords": ("ollama",),
        "env_key": "OLLAMA_API_KEY",
        "litellm_prefix": "ollama",
    },
    "anthropic": {
        "keywords": ("claude", "anthropic"),
        "env_key": "ANTHROPIC_API_KEY",
        "litellm_prefix": "anthropic",
    },
    "deepseek": {
        "keywords": ("deepseek",),
        "env_key": "DEEPSEEK_API_KEY",
        "litellm_prefix": "deepseek",
    },
    "gemini": {
        "keywords": ("gemini", "google"),
        "env_key": "GEMINI_API_KEY",
        "litellm_prefix": "gemini",
    },
    "openai": {
        "keywords": ("gpt", "o1", "o3", "o4"),
        "env_key": "OPENAI_API_KEY",
        "litellm_prefix": "",
    },
    "moonshot": {
        "keywords": ("moonshot", "kimi"),
        "env_key": "MOONSHOT_API_KEY",
        "litellm_prefix": "moonshot",
    },
    "zhipu": {
        "keywords": ("glm", "zhipu"),
        "env_key": "ZHIPUAI_API_KEY",
        "litellm_prefix": "zhipu",
    },
    "dashscope": {
        "keywords": ("qwen", "dashscope"),
        "env_key": "DASHSCOPE_API_KEY",
        "litellm_prefix": "dashscope",
    },
    "minimax": {
        "keywords": ("minimax",),
        "env_key": "MINIMAX_API_KEY",
        "litellm_prefix": "minimax",
    },
}


def detect_provider_by_model(model: str) -> str | None:
    """Detect provider by model name."""
    model_lower = model.lower()
    for provider, config in PROVIDER_CONFIGS.items():
        if any(kw in model_lower for kw in config["keywords"]):
            return provider
    return None


class LiteLLMVLMProvider(VLMBase):
    """
    Multi-provider VLM implementation based on LiteLLM.

    Supports various providers through LiteLLM's unified interface.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self._provider_name = config.get("provider")
        self._extra_headers = config.get("extra_headers") or {}
        self._thinking = config.get("thinking", False)
        self._detected_provider: str | None = None

        if self.api_key:
            self._setup_env(self.api_key, self.model)

        # Configure LiteLLM behavior (these are global but safe to re-set)
        litellm.suppress_debug_info = True
        litellm.drop_params = True

    def _setup_env(self, api_key: str, model: str | None) -> None:
        """Set environment variables based on detected provider."""
        provider = self._provider_name
        if (not provider or provider == "litellm") and model:
            detected = detect_provider_by_model(model)
            if detected:
                provider = detected

        if provider and provider in PROVIDER_CONFIGS:
            env_key = PROVIDER_CONFIGS[provider]["env_key"]
            os.environ[env_key] = api_key
            self._detected_provider = provider
        else:
            # Fallback to OpenAI if provider is unknown or literal litellm
            os.environ["OPENAI_API_KEY"] = api_key

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider prefixes."""
        provider = self._detected_provider or detect_provider_by_model(model)

        if provider and provider in PROVIDER_CONFIGS:
            prefix = PROVIDER_CONFIGS[provider]["litellm_prefix"]
            # LiteLLM uses the `zai/` prefix for Zhipu GLM; do not prepend `zhipu/` (see #784).
            is_zhipu_zai_model = provider == "zhipu" and model.startswith("zai/")
            if prefix and not model.startswith(f"{prefix}/") and not is_zhipu_zai_model:
                return f"{prefix}/{model}"
            return model

        if self.api_base and not model.startswith(("openai/", "hosted_vllm/", "ollama/")):
            return f"openai/{model}"

        return model

    def _detect_image_format(self, data: bytes) -> str:
        """Detect image format from magic bytes.

        Supported formats: PNG, JPEG, GIF, WebP
        """
        if len(data) < 8:
            logger.warning(f"[LiteLLMVLM] Image data too small: {len(data)} bytes")
            return "image/png"

        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        elif data[:2] == b"\xff\xd8":
            return "image/jpeg"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        elif data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
            return "image/webp"

        logger.warning(f"[LiteLLMVLM] Unknown image format, magic bytes: {data[:8].hex()}")
        return "image/png"

    def _prepare_image(self, image: Union[str, Path, bytes]) -> Dict[str, Any]:
        """Prepare image data for vision completion."""
        if isinstance(image, bytes):
            b64 = base64.b64encode(image).decode("utf-8")
            mime_type = self._detect_image_format(image)
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
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
                data = f.read()
            b64 = base64.b64encode(data).decode("utf-8")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            }
        else:
            return {"type": "image_url", "image_url": {"url": image}}

    def _build_kwargs(
        self,
        model: str,
        messages: list,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        thinking: bool = False,
    ) -> dict[str, Any]:
        """Build kwargs for LiteLLM call."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens

        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            # For Gemini, LiteLLM constructs the URL itself. If user provides a full Google endpoint
            # as api_base, it might break the URL construction in LiteLLM.
            # We only pass api_base if it doesn't look like a standard Google endpoint versioned URL.
            is_google_endpoint = "generativelanguage.googleapis.com" in self.api_base and (
                "/v1" in self.api_base or "/v1beta" in self.api_base
            )
            if not is_google_endpoint:
                kwargs["api_base"] = self.api_base
        if self._extra_headers:
            kwargs["extra_headers"] = self._extra_headers
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        # Only send enable_thinking to DashScope-compatible providers
        provider = self._detected_provider or detect_provider_by_model(model)
        if provider == "dashscope":
            extra = kwargs.get("extra_body", {})
            extra["enable_thinking"] = thinking
            kwargs["extra_body"] = extra

        return kwargs

    def _parse_tool_calls(self, message) -> List[ToolCall]:
        """Parse tool calls from LiteLLM response message."""
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return tool_calls

    def _build_vlm_response(self, response, has_tools: bool) -> Union[str, VLMResponse]:
        """Build response from LiteLLM response. Returns str or VLMResponse based on has_tools."""
        choice = response.choices[0]
        message = choice.message

        if has_tools:
            usage = {}
            if hasattr(response, "usage") and response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "prompt_tokens_details": getattr(response.usage, "prompt_tokens_details", None),
                }

            return VLMResponse(
                content=message.content,
                tool_calls=self._parse_tool_calls(message),
                finish_reason=choice.finish_reason or "stop",
                usage=usage,
            )
        else:
            return message.content or ""

    def get_completion(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion synchronously."""
        model = self._resolve_model(self.model or "gpt-4o-mini")
        if messages:
            kwargs_messages = messages
        else:
            kwargs_messages = [{"role": "user", "content": prompt}]

        kwargs = self._build_kwargs(model, kwargs_messages, tools, tool_choice, thinking=thinking)

        t0 = time.perf_counter()
        response = completion(**kwargs)
        elapsed = time.perf_counter() - t0
        self._update_token_usage_from_response(response, duration_seconds=elapsed)
        return self._build_vlm_response(response, has_tools=bool(tools))

    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: bool = False,
        max_retries: int = 0,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion asynchronously."""
        model = self._resolve_model(self.model or "gpt-4o-mini")
        if messages:
            kwargs_messages = messages
        else:
            kwargs_messages = [{"role": "user", "content": prompt}]

        kwargs = self._build_kwargs(model, kwargs_messages, tools, tool_choice, thinking=thinking)

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                t0 = time.perf_counter()
                response = await acompletion(**kwargs)
                elapsed = time.perf_counter() - t0
                self._update_token_usage_from_response(
                    response,
                    duration_seconds=elapsed,
                )
                return self._build_vlm_response(response, has_tools=bool(tools))
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)

        if last_error:
            raise last_error
        raise RuntimeError("Unknown error in async completion")

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion synchronously."""
        model = self._resolve_model(self.model or "gpt-4o-mini")

        if messages:
            kwargs_messages = messages
        else:
            content = []
            if images:
                for img in images:
                    content.append(self._prepare_image(img))
            if prompt:
                content.append({"type": "text", "text": prompt})
            kwargs_messages = [{"role": "user", "content": content}]

        kwargs = self._build_kwargs(model, kwargs_messages, tools, thinking=thinking)

        t0 = time.perf_counter()
        response = completion(**kwargs)
        elapsed = time.perf_counter() - t0
        self._update_token_usage_from_response(response, duration_seconds=elapsed)
        return self._build_vlm_response(response, has_tools=bool(tools))

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion asynchronously."""
        model = self._resolve_model(self.model or "gpt-4o-mini")

        if messages:
            kwargs_messages = messages
        else:
            content = []
            if images:
                for img in images:
                    content.append(self._prepare_image(img))
            if prompt:
                content.append({"type": "text", "text": prompt})
            kwargs_messages = [{"role": "user", "content": content}]

        kwargs = self._build_kwargs(model, kwargs_messages, tools, thinking=thinking)

        t0 = time.perf_counter()
        response = await acompletion(**kwargs)
        elapsed = time.perf_counter() - t0
        self._update_token_usage_from_response(response, duration_seconds=elapsed)
        return self._build_vlm_response(response, has_tools=bool(tools))

    def _update_token_usage_from_response(
        self,
        response,
        duration_seconds: float = 0.0,
    ) -> None:
        """Update token usage from response."""
        if hasattr(response, "usage") and response.usage:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            self.update_token_usage(
                model_name=self.model or "unknown",
                provider=self.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_seconds=duration_seconds,
            )
