# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VolcEngine VLM backend implementation."""

import base64
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from openviking.utils.model_retry import retry_async, retry_sync
from openviking_cli.utils import run_async

from ..base import ToolCall, VLMResponse
from .openai_vlm import OpenAIVLM

logger = logging.getLogger(__name__)


class LRUCache:
    """Simple LRU cache implementation."""

    def __init__(self, maxsize: int = 100):
        self._cache = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Optional[str]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def set(self, key: str, value: str) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()


class VolcEngineVLM(OpenAIVLM):
    """VolcEngine VLM backend with prompt caching support."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._sync_client = None
        self._async_client = None
        self.provider = "volcengine"
        self._response_cache = LRUCache(maxsize=100)

        if not self.api_base:
            self.api_base = "https://ark.cn-beijing.volces.com/api/v3"
        if not self.model:
            self.model = "doubao-seed-2-0-pro-260215"

    def _get_response_id_cache_key(self, messages: List[Dict[str, Any]]) -> str:
        """Generate cache key for response_id using JSON serialization."""
        key_messages = []
        for msg in messages:
            filtered = {k: v for k, v in msg.items() if k != "cache_control"}
            key_messages.append(filtered)
        return json.dumps(key_messages, ensure_ascii=False, sort_keys=True)

    def _parse_messages_with_breakpoints(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[List[List[Dict[str, Any]]], List[Dict[str, Any]]]:
        """Split messages into cacheable prefixes and dynamic suffix."""
        first_breakpoint_idx = -1
        for i, msg in enumerate(messages):
            if msg.get("cache_control"):
                first_breakpoint_idx = i
                break

        if first_breakpoint_idx > 0:
            static_segment = messages[: first_breakpoint_idx + 1]
            dynamic_messages = messages[first_breakpoint_idx + 1 :]
            return [static_segment], dynamic_messages

        return [], messages

    async def _get_or_create_from_segments(
        self, segments: List[List[Dict[str, Any]]], end_idx: int
    ) -> Optional[str]:
        """Recursively get or create cached prefixes."""
        if end_idx <= 0:
            return None

        def segments_to_messages(segs: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
            msgs: List[Dict[str, Any]] = []
            for seg in segs:
                msgs.extend(seg)
            return msgs

        prefix = segments_to_messages(segments[:end_idx])
        if end_idx == 1:
            return await self._get_or_create_from_messages(prefix)

        previous_response_id = await self._get_or_create_from_segments(segments, end_idx - 1)
        return await self._get_or_create_from_messages(
            segments_to_messages(segments[end_idx - 1 : end_idx]),
            previous_response_id=previous_response_id,
        )

    async def _get_or_create_from_messages(
        self, messages: List[Dict[str, Any]], previous_response_id: Optional[str] = None
    ) -> Optional[str]:
        """Create a cached prefix and return its response id."""
        cache_key = self._get_response_id_cache_key(messages)
        cached_id = self._response_cache.get(cache_key)
        if cached_id is not None:
            return cached_id

        client = self.get_async_client()
        input_data = self._convert_messages_to_input(messages)
        try:
            response = await client.responses.create(
                model=self.model,
                previous_response_id=previous_response_id,
                input=input_data,
                caching={"type": "enabled", "prefix": True},
                thinking={"type": "disabled"},
            )
            cached_id = response.id
            self._response_cache.set(cache_key, cached_id)
            return cached_id
        except Exception as e:
            logger.warning("[VolcEngineVLM] Failed to create cached prefix: %s", e)
            return None

    async def responseapi_prefixcache_completion(
        self,
        static_segments: List[List[Dict[str, Any]]],
        dynamic_messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        thinking: bool = False,
    ) -> Any:
        """Call VolcEngine Responses API with optional prefix caching."""
        if static_segments:
            response_id = await self._get_or_create_from_segments(
                static_segments, len(static_segments)
            )
        else:
            response_id = None

        client = self.get_async_client()
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "input": self._convert_messages_to_input(dynamic_messages),
            "temperature": self.temperature,
            "thinking": {"type": "enabled" if thinking else "disabled"},
            "caching": {"type": "enabled"},
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if response_format:
            kwargs["text"] = {"format": response_format}
        if response_id:
            kwargs["previous_response_id"] = response_id
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = tool_choice or "auto"

        return await client.responses.create(**kwargs)

    def get_client(self):
        """Get sync client."""
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
        """Get async client."""
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

    def _update_token_usage_from_response(
        self,
        response,
        duration_seconds: float = 0.0,
    ) -> None:
        """Update token usage from either Responses API or chat completions."""
        if hasattr(response, "usage") and response.usage:
            usage = response.usage
            if hasattr(usage, "input_tokens") or hasattr(usage, "output_tokens"):
                prompt_tokens = getattr(usage, "input_tokens", 0) or 0
                completion_tokens = getattr(usage, "output_tokens", 0) or 0
                self.update_token_usage(
                    model_name=self.model or "unknown",
                    provider=self.provider,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    duration_seconds=duration_seconds,
                )
                return
        super()._update_token_usage_from_response(response, duration_seconds=duration_seconds)

    def _build_vlm_response(self, response, has_tools: bool) -> Union[str, VLMResponse]:
        """Build a VLM response from Responses API or chat completions payloads."""
        if hasattr(response, "choices"):
            return super()._build_vlm_response(response, has_tools)

        content = ""
        tool_calls: List[ToolCall] = []
        finish_reason = "stop"

        if hasattr(response, "output") and response.output:
            for item in response.output:
                item_type = getattr(item, "type", None)
                if item_type == "function_call":
                    args = item.arguments
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"raw": args}
                    tool_calls.append(
                        ToolCall(id=item.call_id or "", name=item.name or "", arguments=args)
                    )
                    finish_reason = "tool_calls"
                    continue

                if item_type != "message":
                    continue

                if hasattr(item, "content"):
                    if isinstance(item.content, list):
                        text_parts = []
                        for block in item.content:
                            if getattr(block, "type", None) == "output_text":
                                text_parts.append(block.text or "")
                            elif hasattr(block, "text"):
                                text_parts.append(block.text or "")
                        content = "".join(text_parts)
                    else:
                        content = item.content or ""

                if hasattr(item, "tool_calls") and item.tool_calls:
                    for tc in item.tool_calls:
                        args = tc.arguments
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {"raw": args}
                        tool_name = getattr(tc, "name", None)
                        if not tool_name and hasattr(tc, "function"):
                            tool_name = tc.function.name
                        tool_calls.append(
                            ToolCall(
                                id=getattr(tc, "id", "") or "", name=tool_name or "", arguments=args
                            )
                        )

                finish_reason = getattr(item, "finish_reason", "stop") or "stop"

        usage: Dict[str, Any] = {}
        if hasattr(response, "usage") and response.usage:
            u = response.usage
            usage = {
                "prompt_tokens": getattr(u, "input_tokens", 0),
                "completion_tokens": getattr(u, "output_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0),
            }
            input_details = getattr(u, "input_tokens_details", None)
            if input_details:
                usage["prompt_tokens_details"] = {
                    "cached_tokens": getattr(input_details, "cached_tokens", 0),
                }

        if has_tools:
            return VLMResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
            )
        return content

    def get_completion(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion via the async Responses API implementation."""
        return run_async(
            self.get_completion_async(
                prompt=prompt,
                thinking=thinking,
                tools=tools,
                tool_choice=tool_choice,
                messages=messages,
            )
        )

    def _convert_messages_to_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI-style messages to VolcEngine Responses API input format."""
        input_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "tool_call" and isinstance(content, dict):
                content_str = json.dumps(content, ensure_ascii=False)
                role = "user"
            else:
                if isinstance(content, list):
                    text_parts = []
                    image_urls = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type", "")
                        if block_type == "text" or "text" in block:
                            text = block.get("text", "")
                            if text:
                                text_parts.append(text)
                        elif block_type == "image_url" or "image_url" in block:
                            image_url = block.get("image_url", {})
                            if isinstance(image_url, dict):
                                url = image_url.get("url", "")
                                if url:
                                    image_urls.append(url)
                        else:
                            text = block.get("text", "")
                            if text:
                                text_parts.append(text)
                    content = " ".join(text_parts)
                    if image_urls:
                        data_urls = [u for u in image_urls if u.startswith("data:")]
                        if data_urls:
                            content = (
                                content
                                + "\n[Images: "
                                + ", ".join([f"data URL ({i + 1})" for i in range(len(data_urls))])
                                + "]"
                            )

                content_str = str(content) if content else "[empty]"
                if not content_str or content_str == "[empty]":
                    continue

                if role == "tool":
                    content_str = f"[Tool Result]\n{content_str}"
                    role = "user"

            input_messages.append({"role": role, "content": content_str})

        return input_messages

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI-style tool format to VolcEngine Responses API format."""
        converted = []
        for tool in tools:
            if not isinstance(tool, dict):
                converted.append(tool)
                continue

            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                converted.append(
                    {
                        "type": "function",
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {}),
                    }
                )
            elif "function" in tool:
                func = tool["function"]
                converted.append(
                    {
                        "type": "function",
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {}),
                    }
                )
            elif tool.get("type") != "function":
                converted.append(
                    {
                        "type": "function",
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    }
                )
            else:
                converted.append(tool)

        return converted

    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion with prompt caching support."""
        kwargs_messages = messages or [{"role": "user", "content": prompt}]
        static_segments, dynamic_messages = self._parse_messages_with_breakpoints(kwargs_messages)

        async def _call() -> Union[str, VLMResponse]:
            response = await self.responseapi_prefixcache_completion(
                static_segments=static_segments,
                dynamic_messages=dynamic_messages,
                response_format=None,
                tools=tools,
                tool_choice=tool_choice,
                thinking=thinking,
            )
            self._update_token_usage_from_response(response, duration_seconds=0.0)
            result = self._build_vlm_response(response, has_tools=bool(tools))
            if tools:
                return result
            return self._clean_response(str(result))

        return await retry_async(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="VolcEngine VLM async completion",
        )

    def _build_vision_kwargs(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
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
            "model": self.model or "doubao-seed-2-0-pro-260215",
            "messages": kwargs_messages,
            "temperature": self.temperature,
            "thinking": {"type": "disabled" if not thinking else "enabled"},
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return kwargs

    def _detect_image_format(self, data: bytes) -> str:
        """Detect image format from magic bytes."""
        if len(data) < 12:
            return "image/png"

        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
            return "image/webp"
        if data[:2] == b"BM":
            return "image/bmp"
        if data[:4] == b"II*\x00" or data[:4] == b"MM\x00*":
            return "image/tiff"
        if data[:4] == b"\x00\x00\x01\x00":
            return "image/ico"
        if data[:4] == b"icns":
            return "image/icns"
        if data[:2] == b"\x01\xda":
            return "image/sgi"
        if data[:8] == b"\x00\x00\x00\x0cjP  " or data[:4] == b"\xff\x4f\xff\x51":
            return "image/jp2"
        if len(data) >= 12 and data[4:8] == b"ftyp":
            brand = data[8:12]
            if brand == b"heic":
                return "image/heic"
            if brand == b"heif" or brand[:3] == b"mif":
                return "image/heif"
        if data[:4] == b"<svg" or (data[:5] == b"<?xml" and b"<svg" in data[:100]):
            raise ValueError(
                "SVG format is not supported by VolcEngine VLM API. "
                "Supported formats: JPEG, PNG, GIF, WEBP, BMP, TIFF, ICO, ICNS, SGI, JPEG2000, HEIC, HEIF"
            )

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
                ".bmp": "image/bmp",
                ".dib": "image/bmp",
                ".tiff": "image/tiff",
                ".tif": "image/tiff",
                ".ico": "image/ico",
                ".icns": "image/icns",
                ".sgi": "image/sgi",
                ".j2c": "image/jp2",
                ".j2k": "image/jp2",
                ".jp2": "image/jp2",
                ".jpc": "image/jp2",
                ".jpf": "image/jp2",
                ".jpx": "image/jp2",
                ".heic": "image/heic",
                ".heif": "image/heif",
            }.get(suffix, "image/png")
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
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
        """Get vision completion through chat completions."""
        client = self.get_client()
        kwargs = self._build_vision_kwargs(prompt, images, thinking, tools, None, messages)

        def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            self._update_token_usage_from_response(response, duration_seconds=elapsed)
            result = self._build_vlm_response(response, has_tools=bool(tools))
            if tools:
                return result
            return self._clean_response(str(result))

        return retry_sync(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="VolcEngine VLM vision completion",
        )

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion asynchronously through chat completions."""
        client = self.get_async_client()
        kwargs = self._build_vision_kwargs(prompt, images, thinking, tools, None, messages)

        async def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = await client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            self._update_token_usage_from_response(response, duration_seconds=elapsed)
            result = self._build_vlm_response(response, has_tools=bool(tools))
            if tools:
                return result
            return self._clean_response(str(result))

        return await retry_async(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="VolcEngine VLM async vision completion",
        )
