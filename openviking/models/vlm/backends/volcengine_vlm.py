# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""VolcEngine VLM backend implementation"""

import asyncio
import base64
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .openai_vlm import OpenAIVLM
from ..base import VLMResponse, ToolCall

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

    def _find_cache_breakpoints(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Find cache_control breakpoints in messages.

        Returns:
            {
                "prefix": [...],  # messages before the last breakpoint
                "breakpoint_index": 2,  # index of last breakpoint message
                "current": [...],  # all messages including last breakpoint
                "use_current_as_key": True/False,  # if True, use "current" for cache key
            }
            or None if no breakpoints found.

        When the breakpoint is at index 0 (first message), we use "current" as the
        cache key since there's nothing before it to cache.
        """
        breakpoint_indices = []

        for i, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content", "")

            # Check cache_control in message
            cache_control = msg.get("cache_control")
            if cache_control and isinstance(cache_control, dict):
                breakpoint_indices.append(i)
                continue

            # Also check inside content blocks (for Claude-style content arrays)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("cache_control"):
                        if i not in breakpoint_indices:
                            breakpoint_indices.append(i)
                        break

        if not breakpoint_indices:
            return None

        # Use the last breakpoint
        last_breakpoint_idx = breakpoint_indices[-1]

        # If breakpoint is at index 0, there's nothing before it
        # So we use "current" (including the breakpoint message) as the cache key
        use_current_as_key = (last_breakpoint_idx == 0)

        return {
            "prefix": messages[:last_breakpoint_idx],
            "breakpoint_index": last_breakpoint_idx,
            "current": messages[:last_breakpoint_idx + 1],
            "use_current_as_key": use_current_as_key,
        }

    def _serialize_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Serialize messages to a string for use as cache key."""
        # Extract role and content (skip cache_control in key)
        key_parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            # Handle content as list (Claude-style)
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "")
                        if text:
                            text_parts.append(text)
                content = " ".join(text_parts)

            key_parts.append(f"{role}:{content}")

        return "|".join(key_parts)

    def _get_cached_response_id(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Get cached response_id for the given messages.

        Logic:
        - Find the last cache_control breakpoint
        - Always use "current" (messages including breakpoint) as cache key for consistency
        - This matches what _cache_response_id uses
        """
        breakpoints = self._find_cache_breakpoints(messages)
        if not breakpoints:
            # logger.info("[VolcEngineVLM] Cache: no breakpoints found")
            return None

        # Always use "current" as cache key - matches what we store
        cache_key = self._serialize_messages(breakpoints["current"])
        # logger.info(f"[VolcEngineVLM] Cache: looking up key={cache_key[:100]}...")
        # logger.info(f"[VolcEngineVLM] Cache: current messages count={len(breakpoints['current'])}")

        result = self._response_cache.get(cache_key)
        # logger.info(f"[VolcEngineVLM] Cache: lookup result={result}")
        return result

    def _cache_response_id(self, messages: List[Dict[str, Any]], response_id: str) -> None:
        """Cache response_id for the given messages.

        The cache key uses "current" (breakpoint message + everything before it).
        This way, subsequent calls with the same prefix can find the cached response.
        """
        breakpoints = self._find_cache_breakpoints(messages)
        if not breakpoints:
            # logger.info("[VolcEngineVLM] Cache: no breakpoints, not caching")
            return

        cache_key = self._serialize_messages(breakpoints["current"])
        # logger.info(f"[VolcEngineVLM] Cache: storing key={cache_key[:100]}..., response_id={response_id}")
        # logger.info(f"[VolcEngineVLM] Cache: current messages count={len(breakpoints['current'])}")
        self._response_cache.set(cache_key, response_id)

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

    def _parse_tool_calls(self, message) -> List[ToolCall]:
        """Parse tool calls from VolcEngine response message."""
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
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
                    tool_name = tc.function.name if hasattr(tc, 'function') else ""
                tool_calls.append(ToolCall(
                    id=tc.id or "",
                    name=tool_name or "",
                    arguments=args
                ))
        return tool_calls

    def _update_token_usage_from_response(
        self, response, duration_seconds: float = 0.0,
    ) -> None:
        """Update token usage from VolcEngine Responses API response."""
        if hasattr(response, "usage") and response.usage:
            u = response.usage
            # Responses API uses input_tokens/output_tokens instead of prompt_tokens/completion_tokens
            prompt_tokens = getattr(u, 'input_tokens', 0) or 0
            completion_tokens = getattr(u, 'output_tokens', 0) or 0
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
        #logger.debug(f"[VolcEngineVLM] Response type: {type(response)}")
        # logger.info(f"[VolcEngineVLM] Full response: {response}")
        if hasattr(response, 'output'):
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

        if hasattr(response, 'output') and response.output:
            for item in response.output:
                item_type = getattr(item, 'type', None)
                # Check if it's a function_call item (Responses API format)
                if item_type == 'function_call':
                    # logger.debug(f"[VolcEngineVLM] Found function_call tool call")
                    args = item.arguments
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"raw": args}
                    tool_calls.append(ToolCall(
                        id=item.call_id or "",
                        name=item.name or "",
                        arguments=args
                    ))
                    finish_reason = "tool_calls"
                # Check if it's a message item (Chat API compatibility)
                elif item_type == 'message':
                    message = item
                    if hasattr(message, 'content'):
                        # Content can be a list or string
                        if isinstance(message.content, list):
                            for block in message.content:
                                if hasattr(block, 'type') and block.type == 'output_text':
                                    content = block.text or ""
                                elif hasattr(block, 'text'):
                                    content = block.text or ""
                        else:
                            content = message.content or ""

                    # Parse tool calls from message
                    if hasattr(message, 'tool_calls') and message.tool_calls:
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
                                tool_name = tc.function.name if hasattr(tc, 'function') else ""
                            tool_calls.append(ToolCall(
                                id=tc.id or "",
                                name=tool_name or "",
                                arguments=args
                            ))

                    finish_reason = getattr(message, 'finish_reason', 'stop') or 'stop'

        # Extract usage
        usage = {}
        if hasattr(response, 'usage') and response.usage:
            u = response.usage
            usage = {
                "prompt_tokens": getattr(u, 'input_tokens', 0),
                "completion_tokens": getattr(u, 'output_tokens', 0),
                "total_tokens": getattr(u, 'total_tokens', 0),
            }
            # Handle cached tokens
            input_details = getattr(u, 'input_tokens_details', None)
            if input_details:
                usage["prompt_tokens_details"] = {
                    "cached_tokens": getattr(input_details, 'cached_tokens', 0),
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

        Uses VolcEngine Responses API for caching support.
        """
        client = self.get_client()
        if messages:
            kwargs_messages = messages
        else:
            kwargs_messages = [{"role": "user", "content": prompt}]

        # Check for cached response_id
        previous_response_id = self._get_cached_response_id(kwargs_messages)

        # Use Responses API for prompt caching
        input_data = self._convert_messages_to_input(kwargs_messages)

        kwargs = {
            "model": self.model or "doubao-seed-2-0-pro-260215",
            "input": input_data,
            "temperature": self.temperature,
            "thinking": {"type": "disabled" if not thinking else "enabled"},
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens

        # VolcEngine limitation: cannot use tools with previous_response_id
        # Solution: First call creates cache with tools, subsequent calls use previous_response_id
        # (server will automatically include tools from cache)
        # logger.info(f"[VolcEngineVLM] Request: tools={bool(tools)}, previous_response_id={previous_response_id}")
        if tools and previous_response_id:
            # Both tools and previous_response_id - use previous_response_id for caching
            # (server will include tools from cached context)
            kwargs["previous_response_id"] = previous_response_id
            kwargs["caching"] = {"type": "enabled"}
            # logger.info(f"[VolcEngineVLM] Using cached response_id with tools: {previous_response_id}")
        elif tools:
            # First call with tools: enable caching (will create cache with tools)
            converted_tools = self._convert_tools(tools)
            kwargs["tools"] = converted_tools
            # logger.debug(f"[VolcEngineVLM] Converted tools: {converted_tools}")
            kwargs["tool_choice"] = tool_choice or "auto"
            kwargs["caching"] = {"type": "enabled"}
        elif previous_response_id:
            # Use cached response (tools are in the cached context)
            kwargs["previous_response_id"] = previous_response_id
            kwargs["caching"] = {"type": "enabled"}
            # logger.info(f"[VolcEngineVLM] Using cached response_id: {previous_response_id}")
        else:
            # Enable caching by default for prompt caching support
            kwargs["caching"] = {"type": "enabled"}

        # logger.info(f"[VolcEngineVLM] Request kwargs: caching={kwargs.get('caching')}, previous_response_id={kwargs.get('previous_response_id')}")

        t0 = time.perf_counter()
        # Use Responses API instead of Chat API
        response = client.responses.create(**kwargs)
        elapsed = time.perf_counter() - t0
        self._update_token_usage_from_response(response, duration_seconds=elapsed)

        # Cache the response_id for future requests
        if hasattr(response, 'id') and response.id:
            self._cache_response_id(kwargs_messages, response.id)
            # logger.info(f"[VolcEngineVLM] Cached response_id: {response.id}")

        return self._build_vlm_response(response, has_tools=bool(tools))

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
                        content = content + "\n[Images: " + ", ".join([f"data URL ({i+1})" for i in range(len(data_urls))]) + "]"

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
            input_messages.append({
                "role": role,
                "content": content_str,
            })

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
                converted.append({
                    "type": "function",  # Keep the type field
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {}),
                })
            elif "function" in tool:
                # Has function but no type
                func = tool["function"]
                converted.append({
                    "type": "function",
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {}),
                })
            else:
                # Already in correct format or other format
                # Ensure it has type: function
                if tool.get("type") != "function":
                    converted.append({
                        "type": "function",
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {}),
                    })
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
        """Get text completion asynchronously with prompt caching support.

        Uses VolcEngine Responses API for caching support.
        """
        client = self.get_async_client()
        if messages:
            kwargs_messages = messages
        else:
            kwargs_messages = [{"role": "user", "content": prompt}]

        # Check for cached response_id
        previous_response_id = self._get_cached_response_id(kwargs_messages)

        # Use Responses API for prompt caching
        # Convert messages to input format
        input_data = self._convert_messages_to_input(kwargs_messages)

        # Build kwargs for Responses API
        kwargs = {
            "model": self.model or "doubao-seed-2-0-pro-260215",
            "input": input_data,
            "temperature": self.temperature,
            "thinking": {"type": "disabled" if not thinking else "enabled"},
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens

        # VolcEngine limitation: cannot use tools with previous_response_id
        # Solution: First call creates cache with tools, subsequent calls use previous_response_id
        # (server will automatically include tools from cache)
        # logger.info(f"[VolcEngineVLM] Request: tools={bool(tools)}, previous_response_id={previous_response_id}")
        if tools and previous_response_id:
            # Both tools and previous_response_id - use previous_response_id for caching
            # (server will include tools from cached context)
            kwargs["previous_response_id"] = previous_response_id
            kwargs["caching"] = {"type": "enabled"}
            # logger.info(f"[VolcEngineVLM] Using cached response_id with tools: {previous_response_id}")
        elif tools:
            # First call with tools: enable caching (will create cache with tools)
            converted_tools = self._convert_tools(tools)
            kwargs["tools"] = converted_tools
            # logger.debug(f"[VolcEngineVLM] Converted tools: {converted_tools}")
            kwargs["tool_choice"] = tool_choice or "auto"
            kwargs["caching"] = {"type": "enabled"}
        elif previous_response_id:
            # Use cached response (tools are in the cached context)
            kwargs["previous_response_id"] = previous_response_id
            kwargs["caching"] = {"type": "enabled"}
            # logger.info(f"[VolcEngineVLM] Using cached response_id: {previous_response_id}")
        else:
            # Enable caching by default for prompt caching support
            kwargs["caching"] = {"type": "enabled"}

        # logger.info(f"[VolcEngineVLM] Request kwargs: caching={kwargs.get('caching')}, previous_response_id={kwargs.get('previous_response_id')}")

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                t0 = time.perf_counter()
                # Use Responses API instead of Chat API
                response = await client.responses.create(**kwargs)
                elapsed = time.perf_counter() - t0
                self._update_token_usage_from_response(
                    response, duration_seconds=elapsed,
                )

                # Cache the response_id for future requests
                if hasattr(response, 'id') and response.id:
                    self._cache_response_id(kwargs_messages, response.id)
                    # logger.info(f"[VolcEngineVLM] Cached response_id: {response.id}")

                return self._build_vlm_response(response, has_tools=bool(tools))
            except Exception as e:
                last_error = e
                # Log token info from error response if available
                error_response = getattr(e, 'response', None)
                if error_response and hasattr(error_response, 'usage'):
                    u = error_response.usage
                    prompt_tokens = getattr(u, 'input_tokens', 0) or 0
                    completion_tokens = getattr(u, 'output_tokens', 0) or 0
                    logger.info(f"[VolcEngineVLM] Error response - Input tokens: {prompt_tokens}, Output tokens: {completion_tokens}")
                logger.warning(f"[VolcEngineVLM] Request failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2**attempt)

        if last_error:
            raise last_error
        else:
            raise RuntimeError("Unknown error in async completion")

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

        Uses VolcEngine Responses API for caching support.
        """
        client = self.get_client()

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

        # Check for cached response_id
        previous_response_id = self._get_cached_response_id(kwargs_messages)

        # Use Responses API
        input_data = self._convert_messages_to_input(kwargs_messages)

        kwargs = {
            "model": self.model or "doubao-seed-2-0-pro-260215",
            "input": input_data,
            "temperature": self.temperature,
            "thinking": {"type": "disabled" if not thinking else "enabled"},
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens

        # VolcEngine limitation: cannot use tools with previous_response_id
        # Solution: First call creates cache with tools, subsequent calls use previous_response_id
        # (server will automatically include tools from cache)
        if tools:
            # Convert tools to VolcEngine format
            converted_tools = self._convert_tools(tools)
            kwargs["tools"] = converted_tools
            # logger.debug(f"[VolcEngineVLM] Converted tools: {converted_tools}")
            kwargs["tool_choice"] = "auto"
            # First call: enable caching (will create cache with tools)
            # Subsequent calls: use previous_response_id (no tools needed, server has them in cache)
            if not previous_response_id:
                kwargs["caching"] = {"type": "enabled"}
        elif previous_response_id:
            # Use cached response (tools are in the cached context)
            kwargs["previous_response_id"] = previous_response_id
            kwargs["caching"] = {"type": "enabled"}
            # logger.info(f"[VolcEngineVLM] Using cached response_id: {previous_response_id}")
        else:
            # Enable caching by default for prompt caching support
            kwargs["caching"] = {"type": "enabled"}

        t0 = time.perf_counter()
        # Use Responses API
        response = client.responses.create(**kwargs)
        elapsed = time.perf_counter() - t0
        self._update_token_usage_from_response(response, duration_seconds=elapsed)

        # Cache the response_id for future requests
        if hasattr(response, 'id') and response.id:
            self._cache_response_id(kwargs_messages, response.id)
            # logger.info(f"[VolcEngineVLM] Cached response_id: {response.id}")

        return self._build_vlm_response(response, has_tools=bool(tools))

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        """Get vision completion asynchronously with prompt caching support.

        Uses VolcEngine Responses API for caching support.
        """
        client = self.get_async_client()

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

        # Check for cached response_id
        previous_response_id = self._get_cached_response_id(kwargs_messages)

        # Use Responses API
        input_data = self._convert_messages_to_input(kwargs_messages)

        kwargs = {
            "model": self.model or "doubao-seed-2-0-pro-260215",
            "input": input_data,
            "temperature": self.temperature,
            "thinking": {"type": "disabled" if not thinking else "enabled"},
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens

        # VolcEngine limitation: cannot use tools with previous_response_id
        # Solution: First call creates cache with tools, subsequent calls use previous_response_id
        # (server will automatically include tools from cache)
        if tools:
            # Convert tools to VolcEngine format
            converted_tools = self._convert_tools(tools)
            kwargs["tools"] = converted_tools
            # logger.debug(f"[VolcEngineVLM] Converted tools: {converted_tools}")
            kwargs["tool_choice"] = "auto"
            # First call: enable caching (will create cache with tools)
            # Subsequent calls: use previous_response_id (no tools needed, server has them in cache)
            if not previous_response_id:
                kwargs["caching"] = {"type": "enabled"}
        elif previous_response_id:
            # Use cached response (tools are in the cached context)
            kwargs["previous_response_id"] = previous_response_id
            kwargs["caching"] = {"type": "enabled"}
            # logger.info(f"[VolcEngineVLM] Using cached response_id: {previous_response_id}")
        else:
            # Enable caching by default for prompt caching support
            kwargs["caching"] = {"type": "enabled"}

        t0 = time.perf_counter()
        # Use Responses API
        response = await client.responses.create(**kwargs)
        elapsed = time.perf_counter() - t0
        self._update_token_usage_from_response(response, duration_seconds=elapsed)

        # Cache the response_id for future requests
        if hasattr(response, 'id') and response.id:
            self._cache_response_id(kwargs_messages, response.id)
            # logger.info(f"[VolcEngineVLM] Cached response_id: {response.id}")

        return self._build_vlm_response(response, has_tools=bool(tools))
