# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangChain chat history backed by OpenViking sessions."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Callable

try:
    from langchain_core.chat_history import BaseChatMessageHistory
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

from openviking.integrations.langchain.client import (
    OpenVikingCommitPolicy,
    OpenVikingConnection,
    call_openviking,
    ensure_client,
    extract_message_text,
    maybe_commit_session,
)

logger = logging.getLogger(__name__)


class OpenVikingChatMessageHistory(BaseChatMessageHistory):
    """LangChain chat history implementation stored in an OpenViking session."""

    def __init__(
        self,
        session_id: str,
        *,
        client: Any = None,
        url: str | None = None,
        api_key: str | None = None,
        account: str | None = None,
        user: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        path: str | None = None,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        auto_initialize: bool = True,
        token_budget: int = 128_000,
        persist_system_messages: bool = False,
        commit_policy: OpenVikingCommitPolicy | None = None,
        context_parts_provider: Callable[[str], list[dict[str, Any]]] | None = None,
    ):
        self.session_id = session_id
        self.token_budget = token_budget
        # Retained for constructor compatibility. System messages are runtime
        # policy, not conversation memory, so they are never persisted.
        self.persist_system_messages = False
        self.commit_policy = commit_policy
        self.context_parts_provider = context_parts_provider
        self._connection = OpenVikingConnection(
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            agent_id=agent_id,
            path=path,
            timeout=timeout,
            extra_headers=extra_headers,
            auto_initialize=auto_initialize,
        )
        self._client_cache: Any = None

    @property
    def messages(self) -> list[BaseMessage]:
        client = self._get_client()
        try:
            context = call_openviking(
                client,
                "get_session_context",
                session_id=self.session_id,
                token_budget=self.token_budget,
            )
        except Exception:
            logger.debug("OpenViking chat history context fetch failed", exc_info=True)
            self._ensure_session(client)
            return []

        return _restore_openviking_messages(context.get("messages") or [])

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        client = self._get_client()
        added = 0
        pending_context_parts = (
            list(self.context_parts_provider(self.session_id))
            if self.context_parts_provider
            else []
        )
        for message in messages:
            for payload in langchain_message_to_openviking(
                message,
                persist_system_messages=self.persist_system_messages,
            ):
                if pending_context_parts and payload["role"] == "assistant":
                    payload["parts"].extend(pending_context_parts)
                    pending_context_parts = []
                call_openviking(
                    client,
                    "add_message",
                    session_id=self.session_id,
                    role=payload["role"],
                    parts=payload["parts"],
                )
                added += 1
        if added:
            maybe_commit_session(client, self.session_id, self.commit_policy)

    def clear(self) -> None:
        client = self._get_client()
        call_openviking(client, "delete_session", session_id=self.session_id)
        self._ensure_session(client)

    def _get_client(self) -> Any:
        if self._client_cache is None:
            self._client_cache = ensure_client(self._connection)
        return self._client_cache

    def _ensure_session(self, client: Any) -> None:
        try:
            call_openviking(client, "create_session", session_id=self.session_id)
        except Exception:
            logger.debug("OpenViking chat history session ensure failed", exc_info=True)
            pass


def langchain_message_to_openviking(
    message: BaseMessage,
    *,
    persist_system_messages: bool = False,
) -> list[dict[str, Any]]:
    """Convert a LangChain message into one or more OpenViking add_message payloads."""

    if isinstance(message, HumanMessage):
        parts = _text_parts(message.content)
        return [{"role": "user", "parts": parts or [{"type": "text", "text": ""}]}]

    if isinstance(message, AIMessage):
        parts = _text_parts(message.content)
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


def _restore_openviking_messages(messages: Sequence[dict[str, Any]]) -> list[BaseMessage]:
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


def context_parts_from_documents(documents: Sequence[Any]) -> list[dict[str, Any]]:
    """Build OpenViking ContextPart dictionaries from LangChain Documents."""

    parts: list[dict[str, Any]] = []
    for doc in documents:
        metadata = getattr(doc, "metadata", {}) or {}
        uri = metadata.get("openviking_uri") or metadata.get("source") or ""
        if not uri:
            continue
        parts.append(
            {
                "type": "context",
                "uri": uri,
                "context_type": metadata.get("openviking_context_type") or "resource",
                "abstract": metadata.get("openviking_abstract")
                or getattr(doc, "page_content", "")[:500],
            }
        )
    return parts


def _text_parts(content: Any) -> list[dict[str, str]]:
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
        return value
    return {"value": value}


def _tool_status(message: ToolMessage) -> str:
    return "error" if getattr(message, "status", "") == "error" else "completed"
