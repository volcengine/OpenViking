# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""VolcEngine VLM backend implementation"""

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openviking.utils.model_retry import retry_async, retry_sync

from ..base import ToolCall, VLMResponse
from .openai_vlm import OpenAIVLM

logger = logging.getLogger(__name__)


class VolcEngineVLM(OpenAIVLM):
    """VolcEngine VLM backend"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._sync_client = None
        self._async_client = None
        self.provider = "volcengine"

        if not self.api_base:
            self.api_base = "https://ark.cn-beijing.volces.com/api/v3"
        if not self.model:
            self.model = "doubao-seed-2-0-pro-260215"

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
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return tool_calls

    def _build_vlm_response(self, response, has_tools: bool) -> Union[str, VLMResponse]:
        """Build response from VolcEngine response. Returns str or VLMResponse based on has_tools."""
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
        return message.content or ""

    def _build_text_kwargs(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        kwargs_messages = messages or [{"role": "user", "content": prompt}]
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
        kwargs = self._build_text_kwargs(prompt, thinking, tools, tool_choice, messages)

        def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            self._update_token_usage_from_response(response, duration_seconds=elapsed)
            if tools:
                return self._build_vlm_response(response, has_tools=True)
            return self._clean_response(self._extract_content_from_response(response))

        return retry_sync(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="VolcEngine VLM completion",
        )

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
        kwargs = self._build_text_kwargs(prompt, thinking, tools, tool_choice, messages)

        async def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = await client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            self._update_token_usage_from_response(response, duration_seconds=elapsed)
            if tools:
                return self._build_vlm_response(response, has_tools=True)
            return self._clean_response(self._extract_content_from_response(response))

        return await retry_async(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="VolcEngine VLM async completion",
        )

    def _detect_image_format(self, data: bytes) -> str:
        """Detect image format from magic bytes.

        Returns the MIME type, or raises ValueError for unsupported formats like SVG.

        Supported formats per VolcEngine docs:
        https://www.volcengine.com/docs/82379/1362931
        - JPEG, PNG, GIF, WEBP, BMP, TIFF, ICO, DIB, ICNS, SGI, JPEG2000, HEIC, HEIF
        """
        if len(data) < 12:
            logger.warning(f"[VolcEngineVLM] Image data too small: {len(data)} bytes")
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
            if brand == b"heif":
                return "image/heif"
            if brand[:3] == b"mif":
                return "image/heif"
        if data[:4] == b"<svg" or (data[:5] == b"<?xml" and b"<svg" in data[:100]):
            raise ValueError(
                "SVG format is not supported by VolcEngine VLM API. "
                "Supported formats: JPEG, PNG, GIF, WEBP, BMP, TIFF, ICO, ICNS, SGI, JPEG2000, HEIC, HEIF"
            )

        logger.warning(f"[VolcEngineVLM] Unknown image format, magic bytes: {data[:16].hex()}")
        return "image/png"

    def _prepare_image(self, image: Union[str, Path, bytes]) -> Dict[str, Any]:
        """Prepare image data"""
        if isinstance(image, bytes):
            b64 = base64.b64encode(image).decode("utf-8")
            mime_type = self._detect_image_format(image)
            logger.info(
                f"[VolcEngineVLM] Preparing image from bytes, size={len(image)}, detected mime={mime_type}"
            )
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
        """Get vision completion"""
        client = self.get_client()
        kwargs = self._build_vision_kwargs(prompt, images, thinking, tools, None, messages)

        def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            self._update_token_usage_from_response(response, duration_seconds=elapsed)
            if tools:
                return self._build_vlm_response(response, has_tools=True)
            return self._clean_response(self._extract_content_from_response(response))

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
        """Get vision completion asynchronously"""
        client = self.get_async_client()
        kwargs = self._build_vision_kwargs(prompt, images, thinking, tools, None, messages)

        async def _call() -> Union[str, VLMResponse]:
            t0 = time.perf_counter()
            response = await client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - t0
            self._update_token_usage_from_response(response, duration_seconds=elapsed)
            if tools:
                return self._build_vlm_response(response, has_tools=True)
            return self._clean_response(self._extract_content_from_response(response))

        return await retry_async(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="VolcEngine VLM async vision completion",
        )
