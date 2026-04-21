# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional


def _convert_content_for_responses(content: Any) -> Any:
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


def _stringify_response_payload(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text_parts: List[str] = []
        for part in value:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"text", "input_text", "output_text"}:
                text = part.get("text")
                if text is not None:
                    text_parts.append(str(text))
        if text_parts:
            return "".join(text_parts)
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _convert_tool_call_history(tool_calls: Any) -> List[Dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []
    converted: List[Dict[str, Any]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name", "") or "").strip()
        if not name:
            continue
        converted.append(
            {
                "type": "function_call",
                "call_id": str(tool_call.get("id", "") or tool_call.get("call_id", "") or ""),
                "name": name,
                "arguments": _stringify_response_payload(function.get("arguments"), default="{}"),
            }
        )
    return converted


def _convert_message_for_responses(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    role = str(message.get("role", "user") or "user")
    content = message.get("content")
    if role == "tool":
        return [
            {
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id", "") or message.get("call_id", "") or ""),
                "output": _stringify_response_payload(content),
            }
        ]
    if role == "assistant":
        converted: List[Dict[str, Any]] = []
        converted_content = _convert_content_for_responses(content)
        if converted_content not in ("", []):
            converted.append({"role": "assistant", "content": converted_content})
        converted.extend(_convert_tool_call_history(message.get("tool_calls")))
        return converted
    normalized_role = role if role in {"user", "assistant"} else "user"
    return [{"role": normalized_role, "content": _convert_content_for_responses(content)}]


def _item_get(obj: Any, key: str, default: Any = None) -> Any:
    value = getattr(obj, key, None)
    if value is None and isinstance(obj, dict):
        value = obj.get(key, default)
    return default if value is None else value


def _build_chat_completion_like_response(final_response: Any, model: str) -> Any:
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


class CodexCompletionsAdapter:
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
            input_messages.extend(_convert_message_for_responses(message))
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
                        content=[
                            SimpleNamespace(type="output_text", text="".join(collected_text_deltas))
                        ],
                    )
                ]
        return _build_chat_completion_like_response(final_response, model)

    def create(self, **kwargs) -> Any:
        if kwargs.get("stream"):
            raise NotImplementedError("Streaming is not supported for openai-codex.")
        response = self._create_response(**kwargs)
        return response


class CodexChatShim:
    def __init__(self, adapter: CodexCompletionsAdapter):
        self.completions = adapter


class CodexAsyncCompletionsAdapter:
    def __init__(self, sync_adapter: CodexCompletionsAdapter):
        self._sync_adapter = sync_adapter

    async def create(self, **kwargs) -> Any:
        if kwargs.get("stream"):
            raise NotImplementedError("Streaming is not supported for openai-codex.")
        response = await asyncio.to_thread(self._sync_adapter._create_response, **kwargs)
        return response


class CodexAsyncChatShim:
    def __init__(self, adapter: CodexAsyncCompletionsAdapter):
        self.completions = adapter
