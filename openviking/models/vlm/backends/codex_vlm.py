# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Codex VLM Backend Integration

This module implements the integration with the Codex provider for Vision-Language Models (VLM).
Unlike standard OpenAI API billing endpoints which use the Chat Completions API, Codex's 
subscription-based endpoints process multimodal (vision/VLM) requests primarily through 
the auxiliary Responses API (`client.responses`).

The complexity in this file arises from the need to shim/adapt standard Chat Completions 
requests (used by OpenViking) into Responses API requests. This involves:
1. Converting `text` and `image_url` parts into `input_text` and `input_image`.
2. Adapting tool calls and schemas.
3. Translating the `client.responses.stream` event stream back into a format 
   compatible with standard Chat Completion responses.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

try:
    import openai
except ImportError:
    openai = None

from .codex_auth import DEFAULT_CODEX_BASE_URL, resolve_codex_runtime_credentials
from .openai_vlm import OpenAIVLM, _build_openai_client_kwargs


def _convert_content_for_responses(content: Any) -> Any:
    """
    Converts standard chat completion content (like `text` and `image_url`) into 
    the format expected by the Responses API (`input_text` and `input_image`).
    
    The Responses API, which the Codex subscription endpoint uses for multimodal inputs,
    requires a different payload structure compared to standard OpenAI vision requests.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content else ""
    converted: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            converted.append({"type": "input_text", "text": part.get("text", "")})
            continue
        if part_type == "image_url":
            image_value = part.get("image_url", {})
            url = image_value.get("url", "") if isinstance(image_value, dict) else str(image_value)
            entry: Dict[str, Any] = {"type": "input_image", "image_url": url}
            if isinstance(image_value, dict) and image_value.get("detail"):
                entry["detail"] = image_value["detail"]
            converted.append(entry)
            continue
        if part_type in {"input_text", "input_image"}:
            converted.append(part)
            continue
        text_value = part.get("text")
        if text_value:
            converted.append({"type": "input_text", "text": text_value})
    return converted or ""


def _convert_tools_for_responses(tools: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(tools, list):
        return None
    converted: List[Dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name", "") or "").strip()
        if not name:
            continue
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {}),
            }
        )
    return converted or None


def _item_get(obj: Any, key: str, default: Any = None) -> Any:
    value = getattr(obj, key, None)
    if value is None and isinstance(obj, dict):
        value = obj.get(key, default)
    return default if value is None else value


def _build_chat_completion_like_response(final_response: Any, model: str) -> Any:
    """
    Translates the final response object from the Responses API back into a
    structure mimicking a standard OpenAI ChatCompletion object. This is crucial
    for compatibility with OpenViking's broader VLM logic.
    """
    text_parts: List[str] = []
    tool_calls: List[Any] = []
    for item in getattr(final_response, "output", []) or []:
        item_type = _item_get(item, "type")
        if item_type == "message":
            for part in _item_get(item, "content", []) or []:
                if _item_get(part, "type") in {"output_text", "text"}:
                    text_parts.append(str(_item_get(part, "text", "")))
            continue
        if item_type == "function_call":
            tool_calls.append(
                SimpleNamespace(
                    id=_item_get(item, "call_id", ""),
                    type="function",
                    function=SimpleNamespace(
                        name=_item_get(item, "name", ""),
                        arguments=_item_get(item, "arguments", "{}"),
                    ),
                )
            )
    usage_raw = getattr(final_response, "usage", None)
    usage = None
    if usage_raw is not None:
        usage = SimpleNamespace(
            prompt_tokens=getattr(usage_raw, "input_tokens", 0),
            completion_tokens=getattr(usage_raw, "output_tokens", 0),
            total_tokens=getattr(usage_raw, "total_tokens", 0),
        )
    message = SimpleNamespace(
        role="assistant",
        content="".join(text_parts).strip() or None,
        tool_calls=tool_calls or None,
    )
    choice = SimpleNamespace(
        index=0,
        message=message,
        finish_reason="tool_calls" if tool_calls else "stop",
    )
    return SimpleNamespace(
        choices=[choice],
        model=model,
        usage=usage,
    )


def _response_to_stream_chunks(response: Any) -> Any:
    content = ""
    if getattr(response, "choices", None):
        message = getattr(response.choices[0], "message", None)
        content = getattr(message, "content", "") or ""
    usage = getattr(response, "usage", None)
    chunks: List[Any] = []
    if content:
        chunks.append(
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=content))],
                usage=None,
            )
        )
    if usage is not None:
        chunks.append(SimpleNamespace(choices=[], usage=usage))
    return iter(chunks)


async def _response_to_async_stream_chunks(response: Any):
    for chunk in _response_to_stream_chunks(response):
        yield chunk


class _CodexCompletionsAdapter:
    """
    An adapter that mimics the standard `client.chat.completions` interface but internally
    routes requests to the `client.responses.stream` endpoint.
    
    This is necessary because the Codex subscription endpoint for VLM operations primarily
    functions via the Responses API, which utilizes streaming and a slightly different 
    payload/event schema compared to the standard OpenAI Chat Completion API.
    """
    def __init__(self, client_factory: Callable[[], Any], model: str):
        self._client_factory = client_factory
        self._model = model

    def _create_response(self, **kwargs) -> Any:
        client = self._client_factory()
        messages = kwargs.get("messages") or []
        model = kwargs.get("model") or self._model
        instructions_parts: List[str] = []
        input_messages: List[Dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "user") or "user")
            content = message.get("content") or ""
            if role in {"system", "developer"}:
                instructions_parts.append(content if isinstance(content, str) else str(content))
                continue
            input_messages.append(
                {
                    "role": role,
                    "content": _convert_content_for_responses(content),
                }
            )
        response_kwargs: Dict[str, Any] = {
            "model": model,
            "instructions": "\n\n".join(part for part in instructions_parts if part).strip()
            or "You are a helpful assistant.",
            "input": input_messages or [{"role": "user", "content": ""}],
            "store": False,
        }
        tools = _convert_tools_for_responses(kwargs.get("tools"))
        if tools:
            response_kwargs["tools"] = tools
        collected_output_items: List[Any] = []
        collected_text_deltas: List[str] = []
        has_function_calls = False
        with client.responses.stream(**response_kwargs) as stream:
            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item is not None:
                        collected_output_items.append(item)
                    continue
                if "output_text.delta" in event_type:
                    delta = getattr(event, "delta", "")
                    if delta:
                        collected_text_deltas.append(delta)
                    continue
                if "function_call" in event_type:
                    has_function_calls = True
            final_response = stream.get_final_response()
        output = getattr(final_response, "output", None)
        if not output:
            if collected_output_items:
                final_response.output = list(collected_output_items)
            elif collected_text_deltas and not has_function_calls:
                final_response.output = [
                    SimpleNamespace(
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[SimpleNamespace(type="output_text", text="".join(collected_text_deltas))],
                    )
                ]
        return _build_chat_completion_like_response(final_response, model)

    def create(self, **kwargs) -> Any:
        response = self._create_response(**kwargs)
        if kwargs.get("stream"):
            return _response_to_stream_chunks(response)
        return response


class _CodexChatShim:
    def __init__(self, adapter: _CodexCompletionsAdapter):
        self.completions = adapter


class _CodexAsyncCompletionsAdapter:
    def __init__(self, sync_adapter: _CodexCompletionsAdapter):
        self._sync_adapter = sync_adapter

    async def create(self, **kwargs) -> Any:
        response = await asyncio.to_thread(self._sync_adapter._create_response, **kwargs)
        if kwargs.get("stream"):
            return _response_to_async_stream_chunks(response)
        return response


class _CodexAsyncChatShim:
    def __init__(self, adapter: _CodexAsyncCompletionsAdapter):
        self.completions = adapter


class CodexVLM(OpenAIVLM):
    def __init__(self, config: Dict[str, Any]):
        normalized = dict(config)
        normalized["provider"] = "openai-codex"
        if not normalized.get("api_base"):
            normalized["api_base"] = DEFAULT_CODEX_BASE_URL
        super().__init__(normalized)
        self._client_signature: tuple[str, str] | None = None

    def _build_responses_client(self, api_key: str, api_base: str):
        kwargs = _build_openai_client_kwargs(
            "openai",
            api_key,
            api_base,
            self.api_version,
            self.extra_headers,
        )
        return openai.OpenAI(**kwargs)

    def _get_or_create_sync_responses_client(self):
        api_key, api_base = self._resolve_runtime_credentials()
        signature = (api_key, api_base)
        if self._sync_client is None or self._client_signature != signature:
            adapter = _CodexCompletionsAdapter(
                lambda: self._build_responses_client(api_key, api_base),
                self.model or "gpt-5.3-codex",
            )
            self._sync_client = SimpleNamespace(chat=_CodexChatShim(adapter))
            self._client_signature = signature
        return self._sync_client

    def _get_or_create_async_responses_client(self):
        # The async path uses a sync Responses client behind asyncio.to_thread so
        # credential refresh and auth-store I/O do not block the event loop.
        if self._async_client is None:
            sync_adapter = _CodexCompletionsAdapter(
                lambda: self._build_responses_client(*self._resolve_runtime_credentials()),
                self.model or "gpt-5.3-codex",
            )
            self._async_client = SimpleNamespace(
                chat=_CodexAsyncChatShim(_CodexAsyncCompletionsAdapter(sync_adapter))
            )
        return self._async_client

    def _resolve_runtime_credentials(self) -> tuple[str, str]:
        explicit_api_key = str(self.config.get("api_key", "") or "").strip()
        explicit_api_base = str(self.config.get("api_base", "") or "").strip().rstrip("/")
        if explicit_api_key:
            self.api_key = explicit_api_key
            self.api_base = explicit_api_base or DEFAULT_CODEX_BASE_URL
            return self.api_key, self.api_base
        credentials = resolve_codex_runtime_credentials()
        self.api_key = credentials["api_key"]
        self.api_base = explicit_api_base or credentials["base_url"]
        return self.api_key, self.api_base

    def get_client(self):
        if openai is None:
            raise ImportError("Please install openai: pip install openai")
        return self._get_or_create_sync_responses_client()

    def get_async_client(self):
        if openai is None:
            raise ImportError("Please install openai: pip install openai")
        return self._get_or_create_async_responses_client()

    def is_available(self) -> bool:
        if str(self.config.get("api_key", "") or "").strip():
            return True
        try:
            resolve_codex_runtime_credentials(refresh_if_expiring=False)
        except Exception:
            return False
        return True
