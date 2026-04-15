# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Kimi Coding VLM backend."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx

from openviking.telemetry import tracer
from openviking.utils.model_retry import retry_async, retry_sync

from ..base import ToolCall, VLMBase, VLMResponse

logger = logging.getLogger(__name__)

DEFAULT_KIMI_API_BASE = "https://api.kimi.com/coding"
DEFAULT_KIMI_MODEL = "kimi-code"
DEFAULT_KIMI_MAX_TOKENS = 32768
DEFAULT_KIMI_USER_AGENT = "claude-code/0.1.0"
ANTHROPIC_VERSION = "2023-06-01"
KIMI_LEGACY_MODEL_ALIASES = {
    "kimi-code": "kimi-for-coding",
    "k2p5": "kimi-for-coding",
}

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.IGNORECASE)
_TOOL_CALLS_SECTION_BEGIN = "<|tool_calls_section_begin|>"
_TOOL_CALLS_SECTION_END = "<|tool_calls_section_end|>"
_TOOL_CALL_BEGIN = "<|tool_call_begin|>"
_TOOL_CALL_ARGUMENT_BEGIN = "<|tool_call_argument_begin|>"
_TOOL_CALL_END = "<|tool_call_end|>"


def _strip_tagged_tool_call_counter(value: str) -> str:
    cleaned = value.strip().replace("\n", "").strip()
    if ":" not in cleaned:
        return cleaned
    return cleaned.rsplit(":", 1)[0]


def _parse_tagged_tool_calls(text: str) -> List[ToolCall]:
    trimmed = text.strip()
    if not trimmed.startswith(_TOOL_CALLS_SECTION_BEGIN) or not trimmed.endswith(
        _TOOL_CALLS_SECTION_END
    ):
        return []

    cursor = len(_TOOL_CALLS_SECTION_BEGIN)
    section_end = len(trimmed) - len(_TOOL_CALLS_SECTION_END)
    tool_calls: List[ToolCall] = []

    while cursor < section_end:
        while cursor < section_end and trimmed[cursor].isspace():
            cursor += 1
        if cursor >= section_end:
            break
        if not trimmed.startswith(_TOOL_CALL_BEGIN, cursor):
            return []

        name_start = cursor + len(_TOOL_CALL_BEGIN)
        arg_marker = trimmed.find(_TOOL_CALL_ARGUMENT_BEGIN, name_start, section_end)
        if arg_marker < 0:
            return []

        raw_id = trimmed[name_start:arg_marker].strip()
        if not raw_id:
            return []

        args_start = arg_marker + len(_TOOL_CALL_ARGUMENT_BEGIN)
        call_end = trimmed.find(_TOOL_CALL_END, args_start, section_end)
        if call_end < 0:
            return []

        raw_args = trimmed[args_start:call_end].strip()
        try:
            arguments = json.loads(raw_args)
        except json.JSONDecodeError:
            return []
        if not isinstance(arguments, dict):
            return []

        tool_calls.append(
            ToolCall(
                id=raw_id,
                name=_strip_tagged_tool_call_counter(raw_id),
                arguments=arguments,
            )
        )
        cursor = call_end + len(_TOOL_CALL_END)

    return tool_calls


class KimiVLM(VLMBase):
    """Anthropic-messages compatible Kimi Coding backend."""

    def __init__(self, config: Dict[str, Any]):
        normalized = dict(config)
        normalized["provider"] = "kimi"
        normalized["api_base"] = str(
            normalized.get("api_base") or DEFAULT_KIMI_API_BASE
        ).rstrip("/")
        normalized.setdefault("model", DEFAULT_KIMI_MODEL)
        extra_headers = dict(normalized.get("extra_headers") or {})
        extra_headers.setdefault("User-Agent", DEFAULT_KIMI_USER_AGENT)
        normalized["extra_headers"] = extra_headers
        super().__init__(normalized)
        self._sync_client = None
        self._async_client = None

    def _resolve_model_name(self) -> str:
        model = str(self.model or DEFAULT_KIMI_MODEL).strip()
        if not model:
            return DEFAULT_KIMI_MODEL
        return KIMI_LEGACY_MODEL_ALIASES.get(model, model)

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "X-API-Key": str(self.api_key or ""),
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_KIMI_USER_AGENT,
        }
        for key, value in (self.extra_headers or {}).items():
            normalized_key = str(key)
            if normalized_key.lower() == "user-agent":
                normalized_key = "User-Agent"
            headers[normalized_key] = str(value)
        return headers

    def _detect_image_format(self, data: bytes) -> str:
        if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if len(data) >= 2 and data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return "image/png"

    def _build_image_block(self, image: Union[str, Path, bytes]) -> Dict[str, Any]:
        if isinstance(image, bytes):
            mime_type = self._detect_image_format(image)
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": base64.b64encode(image).decode("utf-8"),
                },
            }

        if isinstance(image, Path) or (
            isinstance(image, str) and not image.startswith(("http://", "https://", "data:"))
        ):
            data = Path(image).read_bytes()
            return self._build_image_block(data)

        if isinstance(image, str) and image.startswith("data:"):
            match = _DATA_URL_RE.match(image)
            if match:
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": match.group("mime"),
                        "data": match.group("data"),
                    },
                }

        if isinstance(image, str) and image.startswith(("http://", "https://")):
            raise ValueError("Kimi Coding requires local image bytes, paths, or data URLs")

        return {"type": "text", "text": str(image)}

    def _convert_openai_tool_calls(self, tool_calls: Any) -> List[Dict[str, Any]]:
        if not isinstance(tool_calls, list):
            return []

        converted: List[Dict[str, Any]] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            raw_arguments = function.get("arguments", {})
            if isinstance(raw_arguments, str):
                try:
                    raw_arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    raw_arguments = {"raw": raw_arguments}
            if not isinstance(raw_arguments, dict):
                raw_arguments = {"raw": raw_arguments}
            converted.append(
                {
                    "type": "tool_use",
                    "id": str(tool_call.get("id", "")),
                    "name": str(function.get("name", "")),
                    "input": raw_arguments,
                }
            )
        return converted

    def _convert_content_to_anthropic(self, content: Any) -> Any:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content) if content is not None else ""

        blocks: List[Dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                blocks.append({"type": "text", "text": part.get("text", "")})
                continue
            if part_type == "image_url":
                image_value = part.get("image_url", {})
                url = image_value.get("url", "") if isinstance(image_value, dict) else str(image_value)
                blocks.append(self._build_image_block(url))
                continue
            if part_type == "image" and isinstance(part.get("source"), dict):
                blocks.append(part)
                continue
            if part_type in {"tool_result", "tool_use"}:
                blocks.append(part)
                continue
            text_value = part.get("text")
            if text_value:
                blocks.append({"type": "text", "text": str(text_value)})
        return blocks or ""

    def _convert_messages(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not messages:
            content: List[Dict[str, Any]] = []
            for image in images or []:
                content.append(self._build_image_block(image))
            if prompt:
                content.append({"type": "text", "text": prompt})
            return [{"role": "user", "content": content or prompt or ""}], None

        anthropic_messages: List[Dict[str, Any]] = []
        system_parts: List[str] = []

        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "user") or "user")
            if role == "tool":
                tool_call_id = str(message.get("tool_call_id", "") or "")
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_call_id,
                                "content": str(message.get("content", "") or ""),
                            }
                        ],
                    }
                )
                continue

            content = self._convert_content_to_anthropic(message.get("content", ""))
            if role in {"system", "developer"}:
                if isinstance(content, str):
                    if content.strip():
                        system_parts.append(content.strip())
                else:
                    text_parts = [
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    joined = "\n\n".join(part for part in text_parts if part)
                    if joined:
                        system_parts.append(joined)
                continue

            if role == "assistant":
                assistant_blocks: List[Dict[str, Any]] = []
                if isinstance(content, str):
                    if content:
                        assistant_blocks.append({"type": "text", "text": content})
                else:
                    assistant_blocks.extend(content)
                # OpenViking mostly uses OpenAI-style tool history, so convert it
                # here instead of relying on each caller to reshape the transcript.
                assistant_blocks.extend(self._convert_openai_tool_calls(message.get("tool_calls")))
                anthropic_messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_blocks or [{"type": "text", "text": " "}],
                    }
                )
                continue

            anthropic_messages.append({"role": "user", "content": content})

        if not anthropic_messages:
            anthropic_messages.append({"role": "user", "content": prompt or ""})
        return anthropic_messages, "\n\n".join(system_parts).strip() or None

    def _convert_tools(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not isinstance(tools, list):
            return None
        converted: List[Dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name", "")).strip()
            if not name:
                continue
            converted.append(
                {
                    "name": name,
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters", {}),
                }
            )
        return converted or None

    def _convert_tool_choice(self, tool_choice: Optional[str]) -> Optional[Dict[str, Any]]:
        if not tool_choice or tool_choice == "auto":
            return None
        if tool_choice == "none":
            return {"type": "none"}
        return {"type": "tool", "name": str(tool_choice)}

    def _build_request_payload(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        converted_messages, system = self._convert_messages(prompt, images, messages)
        payload: Dict[str, Any] = {
            "model": self._resolve_model_name(),
            "messages": converted_messages,
            "max_tokens": int(self.max_tokens or DEFAULT_KIMI_MAX_TOKENS),
        }
        if system:
            payload["system"] = system
        converted_tools = self._convert_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
            resolved_tool_choice = self._convert_tool_choice(tool_choice)
            if resolved_tool_choice is not None:
                payload["tool_choice"] = resolved_tool_choice
        return payload

    def _update_usage(self, usage: Dict[str, Any], duration_seconds: float = 0.0) -> None:
        if not usage:
            return
        prompt_tokens = int(usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("output_tokens", 0) or 0)
        if prompt_tokens or completion_tokens:
            self.update_token_usage(
                model_name=self._resolve_model_name(),
                provider=self.provider,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_seconds=duration_seconds,
            )

    def _build_response(self, data: Dict[str, Any], has_tools: bool) -> Union[str, VLMResponse]:
        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []

        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = str(block.get("text", "") or "")
                tagged_tool_calls = _parse_tagged_tool_calls(text)
                if tagged_tool_calls:
                    tool_calls.extend(tagged_tool_calls)
                elif text:
                    text_parts.append(text)
                continue
            if block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        arguments=block.get("input") or {},
                    )
                )

        usage = data.get("usage") or {}
        finish_reason = str(data.get("stop_reason") or "stop")
        if finish_reason == "tool_use" or tool_calls:
            finish_reason = "tool_calls"

        if has_tools:
            return VLMResponse(
                content=self._clean_response("".join(text_parts)) if text_parts else None,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage={
                    "prompt_tokens": int(usage.get("input_tokens", 0) or 0),
                    "completion_tokens": int(usage.get("output_tokens", 0) or 0),
                    "total_tokens": int(
                        (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0)
                    ),
                },
            )

        return self._clean_response("".join(text_parts))

    def _post_messages(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        client = self.get_client()

        def _call() -> Dict[str, Any]:
            t0 = time.perf_counter()
            response = client.post(
                f"{self.api_base}/v1/messages",
                headers=self._build_headers(),
                json=payload,
            )
            elapsed = time.perf_counter() - t0
            response.raise_for_status()
            data = response.json()
            self._update_usage(data.get("usage") or {}, duration_seconds=elapsed)
            return data

        return retry_sync(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="Kimi VLM completion",
        )

    async def _post_messages_async(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        client = self.get_async_client()

        async def _call() -> Dict[str, Any]:
            t0 = time.perf_counter()
            response = await client.post(
                f"{self.api_base}/v1/messages",
                headers=self._build_headers(),
                json=payload,
            )
            elapsed = time.perf_counter() - t0
            response.raise_for_status()
            data = response.json()
            self._update_usage(data.get("usage") or {}, duration_seconds=elapsed)
            return data

        return await retry_async(
            _call,
            max_retries=self.max_retries,
            logger=logger,
            operation_name="Kimi VLM async completion",
        )

    def get_client(self):
        if self._sync_client is None:
            self._sync_client = httpx.Client(timeout=60.0)
        return self._sync_client

    def get_async_client(self):
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=60.0)
        return self._async_client

    def get_completion(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        del thinking
        payload = self._build_request_payload(
            prompt=prompt,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )
        data = self._post_messages(payload)
        return self._build_response(data, has_tools=bool(tools))

    @tracer("kimi.vlm.call", ignore_result=True, ignore_args=["messages"])
    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        del thinking
        payload = self._build_request_payload(
            prompt=prompt,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )
        tracer.info(f"request: {json.dumps(payload, ensure_ascii=False, indent=2)}")
        data = await self._post_messages_async(payload)
        return self._build_response(data, has_tools=bool(tools))

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        del thinking
        payload = self._build_request_payload(
            prompt=prompt,
            images=images,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )
        data = self._post_messages(payload)
        return self._build_response(data, has_tools=bool(tools))

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[List[Union[str, Path, bytes]]] = None,
        thinking: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, VLMResponse]:
        del thinking
        payload = self._build_request_payload(
            prompt=prompt,
            images=images,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )
        data = await self._post_messages_async(payload)
        return self._build_response(data, has_tools=bool(tools))
