# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenAI VLM backend implementation"""

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse


from openviking.telemetry import tracer

try:
    import openai
except ImportError:
    openai = None

from openviking.utils.model_retry import retry_async, retry_sync

from ..base import ToolCall, VLMBase, VLMResponse
from ..registry import DEFAULT_AZURE_API_VERSION

logger = logging.getLogger(__name__)


_DASHSCOPE_HOSTS = {
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
}


def _build_openai_client_kwargs(
    provider: str,
    api_key: str,
    api_base: str,
    api_version: str | None,
    extra_headers: Dict[str, str] | None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Build kwargs dict shared by sync and async OpenAI/Azure client constructors."""
    if provider == "azure":
        if not api_base:
            raise ValueError("api_base (Azure endpoint) is required for Azure provider")
        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "azure_endpoint": api_base,
            "api_version": api_version or DEFAULT_AZURE_API_VERSION,
            "timeout": timeout,
        }
    else:
        kwargs = {"api_key": api_key, "base_url": api_base}
    kwargs["timeout"] = timeout
    if extra_headers:
        kwargs["default_headers"] = extra_headers
    return kwargs


class OpenAIVLM(VLMBase):
    """OpenAI / Azure OpenAI VLM backend"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._sync_client = None
        self._async_client = None
        self.api_version = config.get("api_version")

    def get_client(self):
        """Get sync client"""
        if self._sync_client is None:
            if openai is None:
                raise ImportError("Please install openai: pip install openai")
            kwargs = _build_openai_client_kwargs(
                self.provider,
                self.api_key,
                self.api_base,
                self.api_version,
                self.extra_headers,
            )
            if self.provider == "azure":
                self._sync_client = openai.AzureOpenAI(**kwargs)
            else:
                self._sync_client = openai.OpenAI(**kwargs)
        return self._sync_client

    def get_async_client(self):
        """Get async client"""
        if self._async_client is None:
            if openai is None:
                raise ImportError("Please install openai: pip install openai")
            kwargs = _build_openai_client_kwargs(
                self.provider,
                self.api_key,
                self.api_base,
                self.api_version,
                self.extra_headers,
            )
            if self.provider == "azure":
                self._async_client = openai.AsyncAzureOpenAI(**kwargs)
            else:
                self._async_client = openai.AsyncOpenAI(**kwargs)
        return self._async_client

    def _supports_enable_thinking(self) -> bool:
        """Return True for OpenAI-compatible DashScope endpoints that accept enable_thinking."""
        if self.provider != "openai":
            return False

        if isinstance(self.model, str) and self.model.lower().startswith("dashscope/"):
            return True

        if not self.api_base:
            return False

        try:
            host = urlparse(self.api_base).hostname or ""
        except ValueError:
            return False

        return host.lower() in _DASHSCOPE_HOSTS

    def _apply_provider_specific_extra_body(self, kwargs: Dict[str, Any], thinking: bool) -> None:
        """Attach provider-specific raw body parameters understood by compatible APIs."""
        if self._supports_enable_thinking():
            kwargs["extra_body"] = {"enable_thinking": bool(thinking)}

    def _update_token_usage_from_response(
        self,
        response,
        duration_seconds: float = 0.0,
    ):
        if hasattr(response, "usage") and response.usage:
            tracer.info(f"response.usage={response.usage}")
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            self.update_token_usage(
                model_name=self.model or "gpt-4o-mini",
                provider=self.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_seconds=duration_seconds,
            )
        return

    def _parse_tool_calls(self, message) -> List[ToolCall]:
        """Parse tool calls from OpenAI response message."""
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
        """Build response from OpenAI response. Returns str or VLMResponse based on has_tools."""
        choice = response.choices[0]
        message = choice.message
        tracer.info(f"result={message.content}")
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
        return message.content or ""

    def _extract_from_chunk(self, chunk):
        """Extract content and usage from a single chunk.

        Returns:
            tuple: (content, prompt_tokens, completion_tokens)
        """
        content = None
        prompt_tokens = 0
        completion_tokens = 0

        if chunk.choices and chunk.choices[0].delta:
            content = getattr(chunk.choices[0].delta, "content", None)

        if hasattr(chunk, "usage") and chunk.usage:
            prompt_tokens = chunk.usage.prompt_tokens or 0
            completion_tokens = chunk.usage.completion_tokens or 0

        return content, prompt_tokens, completion_tokens

    def _process_streaming_response(self, response):
        """Process streaming response and extract content and token usage."""
        content_parts = []
        prompt_tokens = 0
        completion_tokens = 0

        for chunk in response:
            content, pt, ct = self._extract_from_chunk(chunk)
            if content:
                content_parts.append(content)
            if pt > 0:
                prompt_tokens = pt
            if ct > 0:
                completion_tokens = ct

        if prompt_tokens > 0 or completion_tokens > 0:
            self.update_token_usage(
                model_name=self.model or "gpt-4o-mini",
                provider=self.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        return "".join(content_parts)

    async def _process_streaming_response_async(self, response):
        """Process async streaming response and extract content and token usage."""
        content_parts = []
        prompt_tokens = 0
        completion_tokens = 0

        async for chunk in response:
            content, pt, ct = self._extract_from_chunk(chunk)
            if content:
                content_parts.append(content)
            if pt > 0:
                prompt_tokens = pt
            if ct > 0:
                completion_tokens = ct

        if prompt_tokens > 0 or completion_tokens > 0:
            self.update_token_usage(
                model_name=self.model or "gpt-4o-mini",
                provider=self.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        return "".join(content_parts)

    def _build_text_kwargs(
        self,
        prompt: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        thinking: bool = False,
    ) -> Dict[str, Any]:
        kwargs_messages = messages or [{"role": "user", "content": prompt}]
        kwargs = {
            "model": self.model or "gpt-4o-mini",
            "messages": kwargs_messages,
            "temperature": self.temperature,
            "stream": self.stream,
        }
        self._apply_provider_specific_extra_body(kwargs, thinking)
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return kwargs

    def _build_vision_kwargs(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        thinking: bool = False,
    ) -> Dict[str, Any]:
        if messages:
            kwargs_messages = messages
        else:
            content = []
            if images:
                content.extend(self._prepare_image(img) for img in images)
            if prompt:
                content.append({"type": "text", "text": prompt})
            kwargs_messages = [{"role": "user", "content": content}]

        kwargs = {
            "model": self.model or "gpt-4o-mini",
            "messages": kwargs_messages,
            "temperature": self.temperature,
            "stream": self.stream,
        }
        self._apply_provider_specific_extra_body(kwargs, thinking)
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return kwargs

    def _extract_completion_content(self, response, elapsed: float) -> str:
        if self.stream:
            content = self._process_streaming_response(response)
        else:
            self._update_token_usage_from_response(response, duration_seconds=elapsed)
            content = self._extract_content_from_response(response)
        return self._clean_response(content)

    async def _extract_completion_content_async(self, response, elapsed: float) -> str:
        if self.stream:
            content = await self._process_streaming_response_async(response)
        else:
            self._update_token_usage_from_response(response, duration_seconds=elapsed)
            content = self._extract_content_from_response(response)
        return self._clean_response(content)

    def get_completion(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion"""
        client = self.get_client()
        kwargs = self._build_text_kwargs(prompt, tools, tool_choice, messages, thinking)

        def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            if tools:
                self._update_token_usage_from_response(response, duration_seconds=elapsed)
                return self._build_vlm_response(response, has_tools=True)
            return self._extract_completion_content(response, elapsed)

        return retry_sync(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="OpenAI VLM completion",
        )

    @tracer("vlm.call", ignore_result=True, ignore_args=["messages"])
    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion asynchronously"""
        client = self.get_async_client()
        kwargs = self._build_text_kwargs(prompt, tools, tool_choice, messages, thinking)

        async def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = await client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            if tools:
                self._update_token_usage_from_response(response, duration_seconds=elapsed)
                return self._build_vlm_response(response, has_tools=True)
            return await self._extract_completion_content_async(response, elapsed)

        # 用 tracer.info 打印请求
        tracer.info(f"messages={json.dumps(kwargs, ensure_ascii=False, indent=2)}")

        return await retry_async(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="OpenAI VLM async completion",
        )

    def _detect_image_format(self, data: bytes) -> str:
        """Detect image format from magic bytes.

        Supported formats: PNG, JPEG, GIF, WebP
        """
        if len(data) < 8:
            logger.warning(f"[OpenAIVLM] Image data too small: {len(data)} bytes")
            return "image/png"

        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
            return "image/webp"

        logger.warning(f"[OpenAIVLM] Unknown image format, magic bytes: {data[:8].hex()}")
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
        if isinstance(image, Path) or (
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
        return {"type": "image_url", "image_url": {"url": image}}

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion"""
        client = self.get_client()
        kwargs = self._build_vision_kwargs(prompt, images, tools, None, messages, thinking)

        def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            if tools:
                self._update_token_usage_from_response(response, duration_seconds=elapsed)
                return self._build_vlm_response(response, has_tools=True)
            return self._extract_completion_content(response, elapsed)

        return retry_sync(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="OpenAI VLM vision completion",
        )

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion asynchronously"""
        client = self.get_async_client()
        kwargs = self._build_vision_kwargs(prompt, images, tools, None, messages, thinking)

        async def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = await client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            if tools:
                self._update_token_usage_from_response(response, duration_seconds=elapsed)
                return self._build_vlm_response(response, has_tools=True)
            return await self._extract_completion_content_async(response, elapsed)

        return await retry_async(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="OpenAI VLM async vision completion",
        )
