# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangChain message conversion and recording filters."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

try:
    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        HumanMessage,
        SystemMessage,
        ToolCall,
        ToolMessage,
    )
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from langchain_openviking.client import missing_dependency

    raise missing_dependency("langchain", "langchain-core") from exc

from langchain_openviking.client import extract_message_text, get_message_role

OPENVIKING_CONTEXT_MARKER = "<openviking_context>"
LANGCHAIN_SUMMARIZATION_SOURCE = "summarization"


def is_recordable_langchain_message(message: BaseMessage) -> bool:
    """Return whether a framework message belongs in OpenViking session history."""

    if isinstance(message, SystemMessage):
        return False
    additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
    if additional_kwargs.get("lc_source") == LANGCHAIN_SUMMARIZATION_SOURCE:
        return False
    if isinstance(message, AIMessage):
        return bool(
            extract_message_text(message.content)
            or message.tool_calls
            or additional_kwargs.get("tool_calls")
        )
    return bool(langchain_message_to_openviking(message))


def is_context_carrier_langchain_message(message: BaseMessage) -> bool:
    """Return whether an otherwise-empty assistant can carry recalled context."""

    if not isinstance(message, AIMessage):
        return False
    additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
    return additional_kwargs.get("lc_source") != LANGCHAIN_SUMMARIZATION_SOURCE


def langchain_message_to_openviking(
    message: BaseMessage,
    *,
    persist_system_messages: bool = False,
) -> list[dict[str, Any]]:
    """Convert a LangChain message into one or more OpenViking message payloads."""

    del persist_system_messages  # Retained for compatibility; system policy is never persisted.

    if isinstance(message, HumanMessage):
        human_parts = _text_parts(message.content)
        return [{"role": "user", "parts": human_parts or [{"type": "text", "text": ""}]}]

    if isinstance(message, AIMessage):
        parts: list[dict[str, Any]] = _text_parts(message.content)
        for tool_call in message.tool_calls or []:
            parts.append(
                {
                    "type": "tool",
                    "tool_id": str(tool_call.get("id") or ""),
                    "tool_name": str(tool_call.get("name") or ""),
                    "tool_input": _tool_args(tool_call.get("args")),
                    "tool_status": "pending",
                }
            )
        return [{"role": "assistant", "parts": parts or [{"type": "text", "text": ""}]}]

    if isinstance(message, ToolMessage):
        return [
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool",
                        "tool_id": str(message.tool_call_id or ""),
                        "tool_name": str(message.name or ""),
                        "tool_output": extract_message_text(message.content),
                        "tool_status": _tool_status(message),
                    }
                ],
            }
        ]

    role = get_message_role(message)
    if role == "system":
        return []

    text = extract_message_text(getattr(message, "content", ""))
    if not text:
        return []
    role = "user" if role == "user" else "assistant"
    return [{"role": role, "parts": [{"type": "text", "text": text}]}]


def openviking_message_to_langchain(message: dict[str, Any]) -> list[BaseMessage]:
    """Convert one OpenViking session message into LangChain messages."""

    role = str(message.get("role") or "")
    parts = list(message.get("parts") or [])
    text = _parts_text(parts)
    if role == "user":
        return [HumanMessage(content=text)]

    tool_calls = []
    tool_call_ids: set[str] = set()
    tool_messages = []
    for part in parts:
        if part.get("type") != "tool":
            continue
        tool_id = str(part.get("tool_id") or "")
        tool_name = str(part.get("tool_name") or "")
        status = str(part.get("tool_status") or "")
        is_completed_result = _has_tool_result(part)
        if not is_completed_result or (
            tool_id and tool_name and part.get("tool_input") is not None
        ):
            if not tool_id or tool_id not in tool_call_ids:
                tool_calls.append(
                    {
                        "id": tool_id,
                        "name": tool_name,
                        "args": _tool_args(part.get("tool_input")),
                    }
                )
                tool_call_ids.add(tool_id)
        if is_completed_result:
            tool_messages.append(
                ToolMessage(
                    content=str(part.get("tool_output") or ""),
                    tool_call_id=tool_id or "openviking-tool",
                    name=tool_name or None,
                    status="error" if status == "error" else "success",
                )
            )

    messages: list[BaseMessage] = []
    if text or tool_calls or not tool_messages:
        messages.append(
            AIMessage(content=text, tool_calls=tool_calls or [])
            if tool_calls
            else AIMessage(content=text)
        )
    messages.extend(tool_messages)
    return messages


def restore_openviking_messages(messages: Sequence[dict[str, Any]]) -> list[BaseMessage]:
    """Restore LangChain history, matching tool results to recorded calls."""

    restored: list[BaseMessage] = []
    active_tool_calls: dict[str, ToolCall] = {}
    completed_tool_calls: dict[str, tuple[ToolCall, ToolMessage]] = {}
    for message in messages:
        converted = openviking_message_to_langchain(message)
        pending_call_ids = {
            str(part.get("tool_id") or "")
            for part in message.get("parts") or []
            if part.get("type") == "tool" and not _has_tool_result(part)
        }
        converted_calls = {
            str(call.get("id") or ""): call
            for item in converted
            if isinstance(item, AIMessage)
            for call in item.tool_calls
        }
        converted_results: dict[str, ToolMessage] = {}
        for item in converted:
            if isinstance(item, ToolMessage):
                converted_results.setdefault(item.tool_call_id, item)
        completed_here: set[str] = set()
        # Complete earlier calls before any new assistant text or calls.
        for result in converted_results.values():
            pending = active_tool_calls.get(result.tool_call_id)
            # Different arguments or names describe a new call that reused the ID.
            if pending is not None and converted_calls.get(result.tool_call_id, pending) == pending:
                restored.append(result)
                completed_tool_calls[result.tool_call_id] = (
                    active_tool_calls.pop(result.tool_call_id),
                    result,
                )
                completed_here.add(result.tool_call_id)
        for langchain_message in converted:
            if isinstance(langchain_message, AIMessage):
                kept: list[ToolCall] = []
                for call in langchain_message.tool_calls:
                    tool_call_id = str(call.get("id") or "")
                    if tool_call_id in completed_here:
                        continue
                    previous = completed_tool_calls.get(tool_call_id)
                    call_result = converted_results.get(tool_call_id)
                    # Only a completed pair can be a repeated stored record.
                    if (
                        tool_call_id not in pending_call_ids
                        and call_result is not None
                        and previous is not None
                        and previous[0] == call
                        and previous[1].content == call_result.content
                        and previous[1].status == call_result.status
                    ):
                        continue
                    kept.append(call)
                if len(kept) != len(langchain_message.tool_calls):
                    if not kept and not langchain_message.content:
                        continue
                    langchain_message = langchain_message.model_copy(update={"tool_calls": kept})
                restored.append(langchain_message)
                for tool_call in langchain_message.tool_calls or []:
                    tool_call_id = str(tool_call.get("id") or "")
                    if tool_call_id:
                        active_tool_calls[tool_call_id] = tool_call
                        completed_tool_calls.pop(tool_call_id, None)
            elif isinstance(langchain_message, ToolMessage):
                tool_call_id = str(langchain_message.tool_call_id or "")
                if tool_call_id and tool_call_id in active_tool_calls:
                    restored.append(langchain_message)
                    completed_tool_calls[tool_call_id] = (
                        active_tool_calls.pop(tool_call_id),
                        langchain_message,
                    )
            else:
                if isinstance(langchain_message, HumanMessage):
                    # Reset duplicate detection; pending calls may still finish later.
                    completed_tool_calls.clear()
                restored.append(langchain_message)
    return restored


def _has_tool_result(part: dict[str, Any]) -> bool:
    return part.get("tool_output") is not None or str(part.get("tool_status") or "") in {
        "completed",
        "error",
    }


def _text_parts(content: Any) -> list[dict[str, Any]]:
    text = extract_message_text(content)
    return [{"type": "text", "text": text}] if text else []


def _parts_text(parts: Sequence[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for part in parts:
        part_type = part.get("type")
        if part_type == "text" and part.get("text"):
            chunks.append(str(part["text"]))
        elif part_type == "context" and part.get("abstract"):
            uri = part.get("uri") or "context"
            chunks.append(f"[context:{uri}] {part['abstract']}")
    return "\n".join(chunks)


def _tool_args(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return deepcopy(value)
    return {"value": deepcopy(value)}


def _tool_status(message: ToolMessage) -> str:
    return "error" if getattr(message, "status", "") == "error" else "completed"
