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
        ToolMessage,
    )
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from openviking.integrations.langchain.client import missing_dependency

    raise missing_dependency("langchain", "langchain-core") from exc

from openviking.integrations.langchain.client import extract_message_text

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

    if isinstance(message, SystemMessage):
        return []

    text = extract_message_text(getattr(message, "content", ""))
    if not text:
        return []
    role = "user" if getattr(message, "type", "") == "human" else "assistant"
    return [{"role": role, "parts": [{"type": "text", "text": text}]}]


def openviking_message_to_langchain(message: dict[str, Any]) -> list[BaseMessage]:
    """Convert one OpenViking session message into LangChain messages."""

    role = str(message.get("role") or "")
    parts = list(message.get("parts") or [])
    text = _parts_text(parts)
    if role == "user":
        return [HumanMessage(content=text)]

    tool_calls = []
    tool_messages = []
    for part in parts:
        if part.get("type") != "tool":
            continue
        tool_id = str(part.get("tool_id") or "")
        tool_name = str(part.get("tool_name") or "")
        status = str(part.get("tool_status") or "")
        has_output = part.get("tool_output") is not None
        is_completed_result = has_output or status in {"completed", "error"}
        if is_completed_result:
            tool_messages.append(
                ToolMessage(
                    content=str(part.get("tool_output") or ""),
                    tool_call_id=tool_id or "openviking-tool",
                    name=tool_name or None,
                    status="error" if status == "error" else "success",
                )
            )
        else:
            tool_calls.append(
                {
                    "id": tool_id,
                    "name": tool_name,
                    "args": _tool_args(part.get("tool_input")),
                }
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
    """Restore a valid LangChain message sequence from OpenViking session messages."""

    restored: list[BaseMessage] = []
    active_tool_call_ids: set[str] = set()
    for message in messages:
        for langchain_message in openviking_message_to_langchain(message):
            if isinstance(langchain_message, AIMessage):
                restored.append(langchain_message)
                for tool_call in langchain_message.tool_calls or []:
                    tool_call_id = str(tool_call.get("id") or "")
                    if tool_call_id:
                        active_tool_call_ids.add(tool_call_id)
            elif isinstance(langchain_message, ToolMessage):
                tool_call_id = str(langchain_message.tool_call_id or "")
                if tool_call_id and tool_call_id in active_tool_call_ids:
                    restored.append(langchain_message)
                    active_tool_call_ids.discard(tool_call_id)
            else:
                restored.append(langchain_message)
    return restored


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
