# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangGraph agent middleware for OpenViking recall and capture."""

from __future__ import annotations

from typing import Any, Callable

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from openviking.integrations.langchain.client import missing_dependency

    raise missing_dependency("langgraph", "langchain/langgraph") from exc

from openviking.integrations.langchain.client import (
    OpenVikingConnection,
    call_openviking,
    ensure_client,
    extract_message_text,
    get_latest_user_text,
)
from openviking.integrations.langchain.retrievers import OpenVikingRetriever

OPENVIKING_CONTEXT_MARKER = "<openviking_context>"


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
        session_id_resolver: Callable[[dict[str, Any], Any], str] | None = None,
        capture_on_after_agent: bool = True,
        commit_on_after_agent: bool = False,
        recall_header: str = "Relevant OpenViking context:",
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
        self.session_id_resolver = session_id_resolver
        self.capture_on_after_agent = capture_on_after_agent
        self.commit_on_after_agent = commit_on_after_agent
        self.recall_header = recall_header
        self._captured_counts: dict[str, int] = {}

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        query = get_latest_user_text(request.messages)
        if not query:
            return handler(request)
        context_block = self._build_context_block(query)
        if not context_block:
            return handler(request)

        system_message = request.system_message
        if system_message is None:
            updated_system = SystemMessage(content=context_block)
        else:
            content = extract_message_text(system_message.content)
            updated_system = SystemMessage(content=f"{content}\n\n{context_block}".strip())
        return handler(request.override(system_message=updated_system))

    def after_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        if not self.capture_on_after_agent:
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        session_id = self._resolve_session_id(state, runtime)
        start = self._captured_counts.get(session_id, 0)
        if start >= len(messages):
            return None

        client = ensure_client(self._connection)
        self._ensure_session(client, session_id)
        added = 0
        for message in messages[start:]:
            role = _message_role(message)
            if role not in {"user", "assistant"}:
                continue
            content = _message_content(message)
            if not content or OPENVIKING_CONTEXT_MARKER in content:
                continue
            call_openviking(
                client,
                "add_message",
                session_id=session_id,
                role=role,
                content=content,
            )
            added += 1
        self._captured_counts[session_id] = len(messages)
        if added and self.commit_on_after_agent:
            call_openviking(client, "commit_session", session_id=session_id)
        return None

    def _build_context_block(self, query: str) -> str:
        try:
            docs = self.retriever.invoke(query)
        except Exception:
            return ""
        if not docs:
            return ""
        chunks = []
        for index, doc in enumerate(docs, start=1):
            uri = doc.metadata.get("openviking_uri") or doc.metadata.get("source") or ""
            chunks.append(f"[{index}] {uri}\n{doc.page_content}".strip())
        return (
            f"{OPENVIKING_CONTEXT_MARKER}\n"
            f"{self.recall_header}\n\n"
            + "\n\n".join(chunks)
            + "\n</openviking_context>"
        )

    def _resolve_session_id(self, state: dict[str, Any], runtime: Any) -> str:
        if self.session_id_resolver:
            return self.session_id_resolver(state, runtime)
        candidates = [
            state.get("thread_id"),
            state.get("session_id"),
            _nested_get(getattr(runtime, "context", None), "thread_id"),
            _nested_get(getattr(runtime, "config", None), "configurable", "thread_id"),
            _nested_get(getattr(runtime, "config", None), "configurable", "session_id"),
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return "langgraph-default"

    def _ensure_session(self, client: Any, session_id: str) -> None:
        try:
            call_openviking(client, "create_session", session_id=session_id)
        except Exception:
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

