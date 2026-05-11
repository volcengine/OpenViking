# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangGraph agent middleware for OpenViking recall and capture."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from openviking.integrations.langchain.client import missing_dependency

    raise missing_dependency("langgraph", "langchain/langgraph") from exc

from openviking.integrations.langchain.client import (
    OpenVikingCommitPolicy,
    OpenVikingConnection,
    call_openviking,
    ensure_client,
    extract_message_text,
    get_latest_user_text,
    maybe_commit_session,
)
from openviking.integrations.langchain.context import (
    OPENVIKING_CONTEXT_MARKER,
    OpenVikingSessionContextAssembler,
)
from openviking.integrations.langchain.history import langchain_message_to_openviking
from openviking.integrations.langchain.retrievers import OpenVikingRetriever

logger = logging.getLogger(__name__)

_SESSION_ID_ERROR = (
    "OpenVikingContextMiddleware requires a LangGraph session id. Pass "
    'config={"configurable": {"thread_id": "..."}}, set state["session_id"], '
    "or provide session_id_resolver."
)


class OpenVikingContextMiddleware(AgentMiddleware):
    """Inject OpenViking recall into LangGraph agent model calls.

    The middleware mirrors the OpenClaw-style lifecycle at LangGraph's extension
    points: recall before model calls and optional session capture after agent
    execution.
    """

    def __init__(
        self,
        *,
        client: Any = None,
        retriever: OpenVikingRetriever | None = None,
        url: str | None = None,
        api_key: str | None = None,
        account: str | None = None,
        user: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        path: str | None = None,
        target_uri: str | list[str] = "",
        limit: int = 5,
        score_threshold: float | None = None,
        token_budget: int = 128_000,
        session_id_resolver: Callable[[dict[str, Any], Any], str] | None = None,
        capture_on_after_agent: bool = True,
        commit_on_after_agent: bool = False,
        commit_policy: OpenVikingCommitPolicy | None = None,
        recall_header: str = "Relevant OpenViking context:",
        include_active_messages: bool = False,
    ):
        super().__init__()
        self._client = client
        self._connection = OpenVikingConnection(
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            agent_id=agent_id,
            path=path,
        )
        self.retriever = retriever or OpenVikingRetriever(
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            agent_id=agent_id,
            path=path,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            search_mode="search",
        )
        self.assembler = OpenVikingSessionContextAssembler(
            client=client,
            retriever=self.retriever,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            agent_id=agent_id,
            path=path,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            token_budget=token_budget,
            include_session_context=True,
            include_active_messages=include_active_messages,
            include_recall=True,
            recall_header=recall_header,
        )
        self.session_id_resolver = session_id_resolver
        self.capture_on_after_agent = capture_on_after_agent
        self.commit_policy = commit_policy
        if commit_on_after_agent and self.commit_policy is None:
            self.commit_policy = OpenVikingCommitPolicy(mode="always")
        self.recall_header = recall_header
        self._captured_signatures: dict[str, tuple[str, ...]] = {}
        self._pending_context_parts: dict[str, list[dict[str, Any]]] = {}

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        query = get_latest_user_text(request.messages)
        if not query:
            return handler(request)
        session_id = self._resolve_session_id(
            getattr(request, "state", {}) or {},
            getattr(request, "runtime", None),
        )
        self._pending_context_parts.pop(session_id, None)
        assembled = self.assembler.assemble(session_id=session_id, query=query)
        context_block = assembled.block
        if not context_block:
            return handler(request)
        if assembled.context_parts:
            self._pending_context_parts[session_id] = assembled.context_parts

        system_message = request.system_message
        if system_message is None:
            updated_system = SystemMessage(content=context_block)
        else:
            content = extract_message_text(system_message.content)
            updated_system = SystemMessage(content=f"{content}\n\n{context_block}".strip())
        try:
            return handler(request.override(system_message=updated_system))
        except Exception:
            self._pending_context_parts.pop(session_id, None)
            raise

    def after_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        if not self.capture_on_after_agent:
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        session_id = self._resolve_session_id(state, runtime)
        previous_signatures = self._captured_signatures.get(session_id, ())
        current_signatures = tuple(_message_signature(message) for message in messages)

        if current_signatures == previous_signatures:
            self._pending_context_parts.pop(session_id, None)
            return None
        start = 0
        if (
            previous_signatures
            and len(current_signatures) > len(previous_signatures)
            and current_signatures[: len(previous_signatures)] == previous_signatures
        ):
            start = len(previous_signatures)

        client = ensure_client(self._connection)
        added = 0
        pending_context_parts = list(self._pending_context_parts.pop(session_id, []))
        for message in messages[start:]:
            if OPENVIKING_CONTEXT_MARKER in _message_content(message):
                continue
            payloads = langchain_message_to_openviking(message)
            for payload in payloads:
                if pending_context_parts and payload["role"] == "assistant":
                    payload["parts"].extend(pending_context_parts)
                    pending_context_parts = []
                call_openviking(
                    client,
                    "add_message",
                    session_id=session_id,
                    role=payload["role"],
                    parts=payload["parts"],
                )
                added += 1
        self._captured_signatures[session_id] = current_signatures
        if added:
            maybe_commit_session(client, session_id, self.commit_policy)
        return None

    def _resolve_session_id(self, state: dict[str, Any], runtime: Any) -> str:
        if self.session_id_resolver:
            resolved = _normalize_session_id(self.session_id_resolver(state, runtime))
            if resolved:
                return resolved
            raise ValueError(_SESSION_ID_ERROR)
        candidates = [
            state.get("thread_id"),
            state.get("session_id"),
            _nested_get(getattr(runtime, "context", None), "thread_id"),
            _nested_get(getattr(runtime, "config", None), "configurable", "thread_id"),
            _nested_get(getattr(runtime, "config", None), "configurable", "session_id"),
        ]
        for candidate in candidates:
            resolved = _normalize_session_id(candidate)
            if resolved:
                return resolved
        raise ValueError(_SESSION_ID_ERROR)

    def _ensure_session(self, client: Any, session_id: str) -> None:
        try:
            call_openviking(client, "create_session", session_id=session_id)
        except Exception:
            logger.debug("OpenViking LangGraph middleware session ensure failed", exc_info=True)
            pass


def _nested_get(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
    return current


def _normalize_session_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _message_role(message: Any) -> str:
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "assistant"
    if isinstance(message, BaseMessage):
        if message.type == "human":
            return "user"
        if message.type == "ai":
            return "assistant"
        return message.type
    if isinstance(message, dict):
        role = str(message.get("role") or message.get("type") or "")
        return {"human": "user", "ai": "assistant"}.get(role, role)
    return str(getattr(message, "role", "") or getattr(message, "type", ""))


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return extract_message_text(message.get("content"))
    return extract_message_text(getattr(message, "content", ""))


def _message_stable_id(message: Any) -> str | None:
    if isinstance(message, dict):
        value = message.get("id")
    else:
        value = getattr(message, "id", None)
    return str(value) if value else None


def _message_signature(message: Any) -> str:
    return _stable_json(
        {
            "id": _message_stable_id(message),
            "role": _message_role(message),
            "content": _message_content(message),
            "tool_calls": _message_tool_calls(message),
            "tool_result": _message_tool_result(message),
        }
    )


def _message_tool_calls(message: Any) -> Any:
    if isinstance(message, AIMessage):
        calls = getattr(message, "tool_calls", None) or []
        if not calls:
            calls = (getattr(message, "additional_kwargs", {}) or {}).get("tool_calls") or []
        return calls
    if isinstance(message, dict):
        return message.get("tool_calls") or []
    return getattr(message, "tool_calls", None) or []


def _message_tool_result(message: Any) -> dict[str, Any]:
    if isinstance(message, ToolMessage):
        return {
            "tool_call_id": getattr(message, "tool_call_id", None),
            "name": getattr(message, "name", None),
            "status": getattr(message, "status", None),
        }
    if isinstance(message, dict):
        return {
            "tool_call_id": message.get("tool_call_id") or message.get("tool_id"),
            "name": message.get("name") or message.get("tool_name"),
            "output": message.get("tool_output") or message.get("output"),
            "status": message.get("status") or message.get("tool_status"),
        }
    return {
        "tool_call_id": getattr(message, "tool_call_id", None),
        "name": getattr(message, "name", None),
        "status": getattr(message, "status", None),
    }


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
