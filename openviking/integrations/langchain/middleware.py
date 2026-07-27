# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangGraph agent middleware for OpenViking recall and capture."""

from __future__ import annotations

import json
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
    extract_message_text,
    get_latest_user_text,
)
from openviking.integrations.langchain.context import OpenVikingSessionContextAssembler
from openviking.integrations.langchain.recording import (
    OpenVikingPartialWriteError,
    OpenVikingSessionRecorder,
)
from openviking.integrations.langchain.retrievers import OpenVikingRetriever

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
        actor_peer_id: str | None = None,
        path: str | None = None,
        target_uri: str | list[str] = "",
        limit: int = 5,
        peer_id: str | None = None,
        score_threshold: float | None = None,
        token_budget: int = 128_000,
        session_id_resolver: Callable[[dict[str, Any], Any], str] | None = None,
        peer_id_resolver: Callable[[dict[str, Any], Any], str | None] | None = None,
        capture_on_after_agent: bool = True,
        commit_on_after_agent: bool = False,
        commit_policy: OpenVikingCommitPolicy | None = None,
        recall_header: str = "Relevant OpenViking context:",
        include_active_messages: bool = False,
    ):
        super().__init__()
        self.recorder = OpenVikingSessionRecorder(
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            actor_peer_id=actor_peer_id,
            path=path,
            commit_policy=None,
        )
        self.retriever = retriever or OpenVikingRetriever(
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            actor_peer_id=actor_peer_id,
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
            actor_peer_id=actor_peer_id,
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
        self.peer_id = peer_id
        self.peer_id_resolver = peer_id_resolver
        self.capture_on_after_agent = capture_on_after_agent
        self.commit_policy = commit_policy
        if commit_on_after_agent and self.commit_policy is None:
            self.commit_policy = OpenVikingCommitPolicy(mode="always")
        self.recall_header = recall_header
        self._captured_signatures: dict[tuple[str, str], tuple[str, ...]] = {}
        self._pending_context_parts: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        query = get_latest_user_text(request.messages)
        if not query:
            return handler(request)
        session_id = self._resolve_session_id(
            getattr(request, "state", {}) or {},
            getattr(request, "runtime", None),
        )
        peer_id = self._resolve_peer_id(
            getattr(request, "state", {}) or {},
            getattr(request, "runtime", None),
        )
        pending_key = _capture_key(session_id, peer_id)
        self._pending_context_parts.pop(pending_key, None)
        assembled = self.assembler.assemble(
            session_id=session_id,
            query=query,
        )
        context_block = assembled.block
        if not context_block:
            return handler(request)
        if assembled.context_parts:
            self._pending_context_parts[pending_key] = assembled.context_parts

        system_message = request.system_message
        if system_message is None:
            updated_system = SystemMessage(content=context_block)
        else:
            content = extract_message_text(system_message.content)
            updated_system = SystemMessage(content=f"{content}\n\n{context_block}".strip())
        try:
            return handler(request.override(system_message=updated_system))
        except Exception:
            self._pending_context_parts.pop(pending_key, None)
            raise

    def after_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        if not self.capture_on_after_agent:
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        session_id = self._resolve_session_id(state, runtime)
        peer_id = self._resolve_peer_id(state, runtime)
        capture_key = _capture_key(session_id, peer_id)
        previous_signatures = self._captured_signatures.get(capture_key, ())
        current_signatures = tuple(_message_signature(message) for message in messages)

        if current_signatures == previous_signatures:
            self.recorder.commit_policy = self.commit_policy
            self.recorder.record(session_id, ())
            self._pending_context_parts.pop(capture_key, None)
            return None
        start = 0
        if (
            previous_signatures
            and len(current_signatures) > len(previous_signatures)
            and current_signatures[: len(previous_signatures)] == previous_signatures
        ):
            start = len(previous_signatures)

        pending_context_parts = list(self._pending_context_parts.get(capture_key, []))
        self.recorder.commit_policy = self.commit_policy
        try:
            result = self.recorder.record(
                session_id,
                messages[start:],
                peer_id=peer_id,
                context_parts=pending_context_parts,
            )
        except OpenVikingPartialWriteError as exc:
            if exc.input_messages_consumed:
                consumed_end = start + exc.input_messages_consumed
                self._captured_signatures[capture_key] = current_signatures[:consumed_end]
            if exc.context_attached:
                self._pending_context_parts.pop(capture_key, None)
            raise

        self._captured_signatures[capture_key] = current_signatures
        if result.context_attached:
            self._pending_context_parts.pop(capture_key, None)
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

    def _resolve_peer_id(self, state: dict[str, Any], runtime: Any) -> str | None:
        if self.peer_id_resolver:
            return _normalize_peer_id(self.peer_id_resolver(state, runtime))
        candidates = [
            state.get("peer_id"),
            state.get("peerId"),
            _nested_get(getattr(runtime, "context", None), "peer_id"),
            _nested_get(getattr(runtime, "context", None), "peerId"),
            _nested_get(getattr(runtime, "config", None), "configurable", "peer_id"),
            _nested_get(getattr(runtime, "config", None), "configurable", "peerId"),
            self.peer_id,
        ]
        for candidate in candidates:
            resolved = _normalize_peer_id(candidate)
            if resolved:
                return resolved
        return None


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


def _normalize_peer_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _capture_key(session_id: str, peer_id: str | None) -> tuple[str, str]:
    return (session_id, peer_id or "")


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
