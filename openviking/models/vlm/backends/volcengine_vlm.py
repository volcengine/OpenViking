# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VolcEngine VLM backend implementation"""

import base64
import json
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Import run_async for sync-to-async calls
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
        # Ensure provider type is correct
        self.provider = "volcengine"

        # Prompt caching: message content -> response_id
        self._response_cache = LRUCache(maxsize=100)

        # VolcEngine-specific defaults
        if not self.api_base:
            self.api_base = "https://ark.cn-beijing.volces.com/api/v3"
        if not self.model:
            self.model = "doubao-seed-2-0-pro-260215"

    def _get_response_id_cache_key(self, messages: List[Dict[str, Any]]) -> str:
        """Generate cache key for response_id using simple JSON serialization."""
        # Filter out cache_control from messages for cache key
        key_messages = []
        for msg in messages:
            filtered = {k: v for k, v in msg.items() if k != "cache_control"}
            key_messages.append(filtered)
        return json.dumps(key_messages, ensure_ascii=False, sort_keys=True)

    def _parse_messages_with_breakpoints(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[List[List[Dict[str, Any]]], List[Dict[str, Any]]]:
        """Parse messages into static segment and dynamic messages.

        Only the content BEFORE the first cache_control becomes the static segment.
        All messages after (including the one with cache_control) become dynamic.
        """
        # 找到第一个 cache_control 的位置
        first_breakpoint_idx = -1
        for i, msg in enumerate(messages):
            if msg.get("cache_control"):
                first_breakpoint_idx = i
                # print(f'cache_control={msg}')
                break

        if first_breakpoint_idx > 0:
            # 有 cache_control，取其之前的内容作为 static segment
            static_segment = messages[: first_breakpoint_idx + 1]
            dynamic_messages = messages[first_breakpoint_idx + 1 :]
            static_segments = [static_segment]
            print(f"static_segment={len(static_segment)}")
            print(f"dynamic_messages={len(dynamic_messages)}")
        else:
            # 没有 cache_control 或在第一个位置，全部作为 dynamic
            static_segments = []
            dynamic_messages = messages

        return static_segments, dynamic_messages

    async def _get_or_create_from_segments(
        self, segments: List[List[Dict[str, Any]]], end_idx: int
    ) -> Optional[str]:
        """递归获取或创建 cache，从长到短尝试。

        Args:
            segments: static 消息分段，每段以 cache_control 结尾
            end_idx: 尝试的前缀长度（包含的 segment 数量）

        Returns:
            response_id for the prefix
        """
        if end_idx <= 0:
            return None

        def segments_to_messages(segs):
            # 拼接前 end_idx 个 segments
            msgs = []
            for seg in segs:
                msgs.extend(seg)
            return msgs

        prefix = segments_to_messages(segments[:end_idx])

        if end_idx == 1:
            response_id = await self._get_or_create_from_messages(prefix)
            return response_id

        previous_response_id = await self._get_or_create_from_segments(segments, end_idx - 1)
        return await self._get_or_create_from_messages(
            segments_to_messages(segments[end_idx - 1 : end_idx]),
            previous_response_id=previous_response_id,
        )

    async def _get_or_create_from_messages(
        self, messages: List[Dict[str, Any]], previous_response_id=None
    ) -> Optional[str]:
        """从头创建新 cache。"""

        # Check cache first
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
            logger.warning(f"[VolcEngineVLM] Failed to create new cache: {e}")
            return None

    async def responseapi_prefixcache_completion(
        self,
        static_segments: List[List[Dict[str, Any]]],
        dynamic_messages: List[Dict[str, Any]],
        response_format: Optional[Dict] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> Any:
        """Use cached response_id for completion with dynamic messages.

        Args:
            static_segments: Multiple static segments, each ending with cache_control
            dynamic_messages: New messages for this request
            response_format: Response format for structured output
            tools: Tool definitions
            tool_choice: Tool choice setting
        """
        # 使用多段缓存获取 response_id
        if static_segments:
            response_id = await self._get_or_create_from_segments(
                static_segments, len(static_segments)
            )
        else:
            response_id = None
        client = self.get_async_client()
        input_data = self._convert_messages_to_input(dynamic_messages)

        kwargs = {
            "model": self.model,
            "input": input_data,
            "temperature": self.temperature,
            "thinking": {"type": "disabled"},
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if response_format:
            kwargs["text"] = {"format": response_format}

        if response_id:
            kwargs["previous_response_id"] = response_id
            kwargs["caching"] = {"type": "enabled"}
        elif tools:
            # First call with tools: enable caching
            converted_tools = self._convert_tools(tools)
            kwargs["tools"] = converted_tools
            kwargs["tool_choice"] = tool_choice or "auto"
            kwargs["caching"] = {"type": "enabled"}
        else:
            # Enable caching by default
            kwargs["caching"] = {"type": "enabled"}

        response = await client.responses.create(**kwargs)
        return response

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

    def _update_token_usage_from_response(
        self,
        response,
        duration_seconds: float = 0.0,
    ) -> None:
        """Update token usage from VolcEngine Responses API response."""
        if hasattr(response, "usage") and response.usage:
            u = response.usage
            # Responses API uses input_tokens/output_tokens instead of prompt_tokens/completion_tokens
            prompt_tokens = getattr(u, "input_tokens", 0) or 0
            completion_tokens = getattr(u, "output_tokens", 0) or 0
            self.update_token_usage(
                model_name=self.model or "unknown",
                provider=self.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_seconds=duration_seconds,
            )
        return

    def _build_vlm_response(self, response, has_tools: bool) -> Union[str, VLMResponse]:
        """Build response from VolcEngine Responses API response.

        Responses API returns:
        - response.output: list of output items
        - response.id: response ID
        - response.usage: token usage
        """
        # Debug: print response structure
        # logger.debug(f"[VolcEngineVLM] Response type: {type(response)}")
        # logger.info(f"[VolcEngineVLM] Full response: {response}")
        if hasattr(response, "output"):
            # logger.debug(f"[VolcEngineVLM] Output items: {len(response.output)}")
            for i, item in enumerate(response.output):
                # logger.debug(f"[VolcEngineVLM]   Item {i}: type={getattr(item, 'type', 'unknown')}")
                # Print full item for debugging
                # logger.info(f"[VolcEngineVLM]   Item {i} full: {item}")
                pass

        # Extract content from Responses API format
        content = ""
        tool_calls = []
        finish_reason = "stop"

        if hasattr(response, "output") and response.output:
            for item in response.output:
                item_type = getattr(item, "type", None)
                # Check if it's a function_call item (Responses API format)
                if item_type == "function_call":
                    # logger.debug(f"[VolcEngineVLM] Found function_call tool call")
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
                # Check if it's a message item (Chat API compatibility)
                elif item_type == "message":
                    message = item
                    if hasattr(message, "content"):
                        # Content can be a list or string
                        if isinstance(message.content, list):
                            for block in message.content:
                                if hasattr(block, "type") and block.type == "output_text":
                                    content = block.text or ""
                                elif hasattr(block, "text"):
                                    content = block.text or ""
                        else:
                            content = message.content or ""

                    # Parse tool calls from message
                    if hasattr(message, "tool_calls") and message.tool_calls:
                        # logger.debug(f"[VolcEngineVLM] Found {len(message.tool_calls)} tool calls in message")
                        for tc in message.tool_calls:
                            args = tc.arguments
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {"raw": args}
                            # Handle both tc.name and tc.function.name (Responses API vs Chat API)
                            try:
                                tool_name = tc.name
                                if not tool_name:
                                    tool_name = tc.function.name
                            except AttributeError:
                                tool_name = tc.function.name if hasattr(tc, "function") else ""
                            tool_calls.append(
                                ToolCall(id=tc.id or "", name=tool_name or "", arguments=args)
                            )

                    finish_reason = getattr(message, "finish_reason", "stop") or "stop"

        # Extract usage
        usage = {}
        if hasattr(response, "usage") and response.usage:
            u = response.usage
            usage = {
                "prompt_tokens": getattr(u, "input_tokens", 0),
                "completion_tokens": getattr(u, "output_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0),
            }
            # Handle cached tokens
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
        else:
            return content

    def get_completion(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion with prompt caching support.

        Uses VolcEngine Responses API with prefix cache.
        Delegates to async implementation.
        """
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
        """Convert OpenAI-style messages to VolcEngine Responses API input format.

        VolcEngine Responses API format (no "type" field needed):
        [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
        ]

        Note: Responses API doesn't support 'tool' role, so we convert tool results
        to user messages with a prefix indicating it's a tool result.
        """
        input_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Handle tool_call role with content as dict {name, args, result}
            if role == "tool_call" and isinstance(content, dict):
                import json

                content_str = json.dumps(content, ensure_ascii=False)
                role = "user"  # Convert tool_call to user
            else:
                # Handle content - check if it contains images
                has_images = False
                if isinstance(content, list):
                    text_parts = []
                    image_urls = []
                    for block in content:
                        if isinstance(block, dict):
                            block_type = block.get("type", "")
                            # Handle text blocks
                            if block_type == "text" or "text" in block:
                                text = block.get("text", "")
                                if text:
                                    text_parts.append(text)
                            # Handle image_url blocks
                            elif block_type == "image_url" or "image_url" in block:
                                image_url = block.get("image_url", {})
                                if isinstance(image_url, dict):
                                    url = image_url.get("url", "")
                                    if url:
                                        image_urls.append(url)
                                has_images = True
                            # Handle other block types
                            else:
                                # Try to extract text from any dict block
                                text = block.get("text", "")
                                if text:
                                    text_parts.append(text)
                    content = " ".join(text_parts)
                    # If there were images, include them as base64 data URLs in content
                    if image_urls:
                        # Filter out non-data URLs (keep only data: URLs)
                        data_urls = [u for u in image_urls if u.startswith("data:")]
                        if data_urls:
                            # Append image references to content
                            content = (
                                content
                                + "\n[Images: "
                                + ", ".join([f"data URL ({i + 1})" for i in range(len(data_urls))])
                                + "]"
                            )

                # Ensure content is a string, use placeholder if empty
                content_str = str(content) if content else "[empty]"
                # Skip messages with empty content (API requirement)
                if not content_str or content_str == "[empty]":
                    continue

                # Handle role conversion
                # Responses API supports: system, user, assistant
                # Convert 'tool' role to user with prefix (preserve the tool result context)
                if role == "tool":
                    # Prefix with tool result indicator
                    content_str = f"[Tool Result]\n{content_str}"
                    role = "user"

            # Simple format: role + content (no type field)
            input_messages.append(
                {
                    "role": role,
                    "content": content_str,
                }
            )

        return input_messages

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI-style tool format to VolcEngine Responses API format.

        OpenAI format: {"type": "function", "function": {"name": ..., "parameters": ...}}
        VolcEngine format: {"type": "function", "name": ..., "description": ..., "parameters": ...}

        Note: VolcEngine Responses API requires "type": "function" and name at top level.
        """
        converted = []
        for tool in tools:
            if not isinstance(tool, dict):
                converted.append(tool)
                continue

            # Check if it's OpenAI format: {"type": "function", "function": {...}}
            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                converted.append(
                    {
                        "type": "function",  # Keep the type field
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {}),
                    }
                )
            elif "function" in tool:
                # Has function but no type
                func = tool["function"]
                converted.append(
                    {
                        "type": "function",
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {}),
                    }
                )
            else:
                # Already in correct format or other format
                # Ensure it has type: function
                if tool.get("type") != "function":
                    converted.append(
                        {
                            "type": "function",
                            "name": tool.get("name", ""),
                            "description": tool.get("description", ""),
                            "parameters": tool.get("parameters", {}),
                        }
                    )
                else:
                    # Keep as is
                    converted.append(tool)

        return converted

    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: bool = False,
        max_retries: int = 0,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get text completion with prompt caching support.

        Uses VolcEngine Responses API with prefix cache.
        Separates messages into static (cached) and dynamic parts.
        """
        if messages:
            kwargs_messages = messages
        else:
            kwargs_messages = [{"role": "user", "content": prompt}]

        # Parse messages into multiple static segments and dynamic messages
        # Each segment ends with cache_control, dynamic is the rest
        static_segments, dynamic_messages = self._parse_messages_with_breakpoints(kwargs_messages)

        # If we have static segments, try prefix cache
        response_format = None  # Can be extended for structured output

        try:
            # Use prefix cache with multiple segments
            response = await self.responseapi_prefixcache_completion(
                static_segments=static_segments,
                dynamic_messages=dynamic_messages,
                response_format=response_format,
                tools=tools,
                tool_choice=tool_choice,
            )
            elapsed = 0  # Timing handled in responseapi methods
            self._update_token_usage_from_response(response, duration_seconds=elapsed)
            return self._build_vlm_response(response, has_tools=bool(tools))

        except Exception as e:
            last_error = e
            # Log token info from error response if available
            error_response = getattr(e, "response", None)
            if error_response and hasattr(error_response, "usage"):
                u = error_response.usage
                prompt_tokens = getattr(u, "input_tokens", 0) or 0
                completion_tokens = getattr(u, "output_tokens", 0) or 0
                logger.info(
                    f"[VolcEngineVLM] Error response - Input tokens: {prompt_tokens}, Output tokens: {completion_tokens}"
                )
            logger.warning(f"[VolcEngineVLM] Request failed: {e}")
            raise last_error

    def _detect_image_format(self, data: bytes) -> str:
        """Detect image format from magic bytes.

        Returns the MIME type, or raises ValueError for unsupported formats like SVG.

        Supported formats per VolcEngine docs:
        https://www.volcengine.com/docs/82379/1362931
        - JPEG, PNG, GIF, WEBP, BMP, TIFF, ICO, DIB, ICNS, SGI, JPEG2000, HEIC, HEIF
        """
        if len(data) < 12:
            # logger.warning(f"[VolcEngineVLM] Image data too small: {len(data)} bytes")
            return "image/png"

        # PNG: 89 50 4E 47 0D 0A 1A 0A
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        # JPEG: FF D8
        elif data[:2] == b"\xff\xd8":
            return "image/jpeg"
        # GIF: GIF87a or GIF89a
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        # WEBP: RIFF....WEBP
        elif data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
            return "image/webp"
        # BMP: BM
        elif data[:2] == b"BM":
            return "image/bmp"
        # TIFF (little-endian): 49 49 2A 00
        # TIFF (big-endian): 4D 4D 00 2A
        elif data[:4] == b"II*\x00" or data[:4] == b"MM\x00*":
            return "image/tiff"
        # ICO: 00 00 01 00
        elif data[:4] == b"\x00\x00\x01\x00":
            return "image/ico"
        # ICNS: 69 63 6E 73 ("icns")
        elif data[:4] == b"icns":
            return "image/icns"
        # SGI: 01 DA
        elif data[:2] == b"\x01\xda":
            return "image/sgi"
        # JPEG2000: 00 00 00 0C 6A 50 20 20 (JP2 signature)
        elif data[:8] == b"\x00\x00\x00\x0cjP  " or data[:4] == b"\xff\x4f\xff\x51":
            return "image/jp2"
        # HEIC/HEIF: ftyp box with heic/heif brand
        # 00 00 00 XX 66 74 79 70 68 65 69 63 (heic)
        # 00 00 00 XX 66 74 79 70 68 65 69 66 (heif)
        elif len(data) >= 12 and data[4:8] == b"ftyp":
            brand = data[8:12]
            if brand == b"heic":
                return "image/heic"
            elif brand == b"heif":
                return "image/heif"
            elif brand[:3] == b"mif":
                return "image/heif"
        # SVG (not supported)
        elif data[:4] == b"<svg" or (data[:5] == b"<?xml" and b"<svg" in data[:100]):
            raise ValueError(
                "SVG format is not supported by VolcEngine VLM API. "
                "Supported formats: JPEG, PNG, GIF, WEBP, BMP, TIFF, ICO, ICNS, SGI, JPEG2000, HEIC, HEIF"
            )

        # Unknown format - log and default to PNG
        # logger.warning(f"[VolcEngineVLM] Unknown image format, magic bytes: {data[:16].hex()}")
        return "image/png"

    def _prepare_image(self, image: Union[str, Path, bytes]) -> Dict[str, Any]:
        """Prepare image data"""
        if isinstance(image, bytes):
            b64 = base64.b64encode(image).decode("utf-8")
            mime_type = self._detect_image_format(image)
            # logger.info(
            # f"[VolcEngineVLM] Preparing image from bytes, size={len(image)}, detected mime={mime_type}"
            # )
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
        else:
            return {"type": "image_url", "image_url": {"url": image}}

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion with prompt caching support.

        Uses VolcEngine Responses API with prefix cache.
        Delegates to async implementation.
        """
        return run_async(
            self.get_vision_completion_async(
                prompt=prompt,
                images=images,
                thinking=thinking,
                tools=tools,
                messages=messages,
            )
        )

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion with prompt caching support.

        Uses VolcEngine Responses API with prefix cache.
        """
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

        # 复用 get_completion_async 的逻辑
        return await self.get_completion_async(
            prompt=prompt,
            thinking=thinking,
            tools=tools,
            messages=kwargs_messages,
        )
