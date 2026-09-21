# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangGraph agent middleware for OpenViking recall and capture."""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Callable

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import AgentState, ModelRequest
    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from langchain_openviking.client import missing_dependency

    raise missing_dependency("langgraph", "langchain/langgraph") from exc

from langchain_openviking.actor_peer import (
    get_actor_peer_id,
    require_request_actor_peer_support,
    use_actor_peer,
)
from langchain_openviking.client import (
    OpenVikingCommitPolicy,
    extract_message_text,
    get_latest_user_text,
)
from langchain_openviking.context import (
    OpenVikingAssembledContext,
    OpenVikingSessionContextAssembler,
)
from langchain_openviking.recording import (
    OpenVikingCancellationProgress,
    OpenVikingPartialWriteError,
    OpenVikingSessionRecorder,
    get_openviking_cancellation_progress,
)
from langchain_openviking.retrievers import OpenVikingRetriever

_SESSION_ID_ERROR = (
    "OpenVikingContextMiddleware requires a LangGraph session id. Pass "
    'config={"configurable": {"thread_id": "..."}}, set state["session_id"], '
    "or provide session_id_resolver."
)
_CaptureKey = tuple[str, str] | tuple[str, str, str]
_ActorPeerResolver = Callable[
    [dict[str, Any], Any],
    str | None,
]


@dataclass(slots=True)
class _CapturePlan:
    session_id: str
    peer_id: str | None
    actor_peer_id: str | None
    key: _CaptureKey
    messages: list[Any]
    signatures: tuple[str, ...]
    start: int
    context_parts: list[dict[str, Any]]
    unchanged: bool = False


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
        async_client: Any = None,
        retriever: OpenVikingRetriever | None = None,
        url: str | None = None,
        api_key: str | None = None,
        account: str | None = None,
        user: str | None = None,
        user_id: str | None = None,
        actor_peer_id: str | None = None,
        target_uri: str | list[str] = "",
        limit: int = 5,
        peer_id: str | None = None,
        score_threshold: float | None = None,
        token_budget: int = 128_000,
        session_id_resolver: Callable[[dict[str, Any], Any], str] | None = None,
        peer_id_resolver: Callable[[dict[str, Any], Any], str | None] | None = None,
        actor_peer_resolver: _ActorPeerResolver | None = None,
        capture_on_after_agent: bool = True,
        commit_on_after_agent: bool = False,
        commit_policy: OpenVikingCommitPolicy | None = None,
        recall_header: str = "Relevant OpenViking context:",
        include_active_messages: bool = False,
    ):
        super().__init__()
        _validate_actor_peer_transport(
            actor_peer_resolver=actor_peer_resolver,
            client=client,
            async_client=async_client,
            retriever=retriever,
        )
        self.recorder = OpenVikingSessionRecorder(
            client=client,
            async_client=async_client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            actor_peer_id=actor_peer_id,
            commit_policy=None,
        )
        self._owns_retriever = retriever is None
        self.retriever = retriever or OpenVikingRetriever(
            client=client,
            async_client=async_client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            actor_peer_id=actor_peer_id,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            search_mode="search",
        )
        self.assembler = OpenVikingSessionContextAssembler(
            client=client,
            async_client=async_client,
            retriever=self.retriever,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            actor_peer_id=actor_peer_id,
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
        self.actor_peer_resolver = actor_peer_resolver
        self.capture_on_after_agent = capture_on_after_agent
        self.commit_policy = commit_policy
        if commit_on_after_agent and self.commit_policy is None:
            self.commit_policy = OpenVikingCommitPolicy(mode="always")
        self.recall_header = recall_header
        self._captured_signatures: dict[_CaptureKey, tuple[str, ...]] = {}
        self._pending_context_parts: dict[_CaptureKey, list[dict[str, Any]]] = {}

    async def aclose(self) -> None:
        """Release all clients internally owned by this middleware."""

        first_error: BaseException | None = None
        components: list[Any] = [self.recorder, self.assembler]
        if self._owns_retriever:
            components.append(self.retriever)
        for component in components:
            try:
                await component.aclose()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        plan = self._model_context_plan(request)
        if plan is None:
            return handler(request)
        session_id, pending_key, query, actor_peer_id = plan
        with self._actor_peer_scope(actor_peer_id):
            assembled = self.assembler.assemble(
                session_id=session_id,
                query=query,
            )
        updated_request = self._request_with_context(request, pending_key, assembled)
        if updated_request is None:
            return handler(request)
        try:
            return handler(updated_request)
        except Exception:
            self._pending_context_parts.pop(pending_key, None)
            raise

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> Any:
        """Asynchronously inject OpenViking context before a model call."""

        plan = self._model_context_plan(request)
        if plan is None:
            return await handler(request)
        session_id, pending_key, query, actor_peer_id = plan
        with self._actor_peer_scope(actor_peer_id):
            assembled = await self.assembler.aassemble(
                session_id=session_id,
                query=query,
            )
        updated_request = self._request_with_context(request, pending_key, assembled)
        if updated_request is None:
            return await handler(request)
        try:
            return await handler(updated_request)
        except BaseException:
            # CancelledError is a BaseException, so cancellation must also discard
            # context references prepared for the interrupted model call.
            self._pending_context_parts.pop(pending_key, None)
            raise

    def _model_context_plan(
        self,
        request: ModelRequest,
    ) -> tuple[str, _CaptureKey, str, str | None] | None:
        query = get_latest_user_text(request.messages)
        if not query:
            return None
        state = getattr(request, "state", {}) or {}
        runtime = getattr(request, "runtime", None)
        session_id = self._resolve_session_id(state, runtime)
        peer_id = self._resolve_peer_id(state, runtime)
        actor_peer_id = self._resolve_actor_peer_id(state, runtime)
        pending_key = _capture_key(session_id, peer_id, actor_peer_id)
        self._pending_context_parts.pop(pending_key, None)
        return session_id, pending_key, query, actor_peer_id

    def _request_with_context(
        self,
        request: ModelRequest,
        pending_key: _CaptureKey,
        assembled: OpenVikingAssembledContext,
    ) -> ModelRequest | None:
        context_block = assembled.block
        if not context_block:
            return None
        if assembled.context_parts:
            self._pending_context_parts[pending_key] = assembled.context_parts

        system_message = request.system_message
        if system_message is None:
            updated_system = SystemMessage(content=context_block)
        else:
            content = extract_message_text(system_message.content)
            updated_system = SystemMessage(content=f"{content}\n\n{context_block}".strip())
        return request.override(system_message=updated_system)

    def after_agent(self, state: AgentState[Any], runtime: Any) -> dict[str, Any] | None:
        plan = self._capture_plan(state, runtime)
        if plan is None:
            return None
        self.recorder.commit_policy = self.commit_policy
        if plan.unchanged:
            with self._actor_peer_scope(plan.actor_peer_id):
                self.recorder.record(plan.session_id, ())
            self._pending_context_parts.pop(plan.key, None)
            return None
        try:
            with self._actor_peer_scope(plan.actor_peer_id):
                result = self.recorder.record(
                    plan.session_id,
                    plan.messages[plan.start :],
                    peer_id=plan.peer_id,
                    context_parts=plan.context_parts,
                )
        except OpenVikingPartialWriteError as exc:
            self._handle_partial_capture(plan, exc)
            raise

        self._complete_capture(plan, context_attached=result.context_attached)
        return None

    async def aafter_agent(
        self,
        state: AgentState[Any],
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Asynchronously capture messages after an agent run."""

        plan = self._capture_plan(state, runtime)
        if plan is None:
            return None
        self.recorder.commit_policy = self.commit_policy
        if plan.unchanged:
            with self._actor_peer_scope(plan.actor_peer_id):
                await self.recorder.arecord(plan.session_id, ())
            self._pending_context_parts.pop(plan.key, None)
            return None
        try:
            with self._actor_peer_scope(plan.actor_peer_id):
                result = await self.recorder.arecord(
                    plan.session_id,
                    plan.messages[plan.start :],
                    peer_id=plan.peer_id,
                    context_parts=plan.context_parts,
                )
        except OpenVikingPartialWriteError as exc:
            self._handle_partial_capture(plan, exc)
            raise
        except asyncio.CancelledError as exc:
            progress = get_openviking_cancellation_progress(exc)
            if progress is not None:
                self._handle_partial_capture(plan, progress)
            raise

        self._complete_capture(plan, context_attached=result.context_attached)
        return None

    def _capture_plan(
        self,
        state: AgentState[Any],
        runtime: Any,
    ) -> _CapturePlan | None:
        if not self.capture_on_after_agent:
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        session_id = self._resolve_session_id(state, runtime)
        peer_id = self._resolve_peer_id(state, runtime)
        actor_peer_id = self._resolve_actor_peer_id(state, runtime)
        key = _capture_key(session_id, peer_id, actor_peer_id)
        previous_signatures = self._captured_signatures.get(key, ())
        signatures = tuple(_message_signature(message) for message in messages)
        unchanged = signatures == previous_signatures
        start = 0
        if (
            not unchanged
            and previous_signatures
            and len(signatures) > len(previous_signatures)
            and signatures[: len(previous_signatures)] == previous_signatures
        ):
            start = len(previous_signatures)
        return _CapturePlan(
            session_id=session_id,
            peer_id=peer_id,
            actor_peer_id=actor_peer_id,
            key=key,
            messages=messages,
            signatures=signatures,
            start=start,
            context_parts=list(self._pending_context_parts.get(key, [])),
            unchanged=unchanged,
        )

    def _handle_partial_capture(
        self,
        plan: _CapturePlan,
        error: OpenVikingPartialWriteError | OpenVikingCancellationProgress,
    ) -> None:
        if error.input_messages_consumed:
            consumed_end = plan.start + error.input_messages_consumed
            self._captured_signatures[plan.key] = plan.signatures[:consumed_end]
        if error.context_attached:
            self._pending_context_parts.pop(plan.key, None)

    def _complete_capture(
        self,
        plan: _CapturePlan,
        *,
        context_attached: bool,
    ) -> None:
        self._captured_signatures[plan.key] = plan.signatures
        if context_attached:
            self._pending_context_parts.pop(plan.key, None)

    def _resolve_session_id(self, state: Any, runtime: Any) -> str:
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

    def _resolve_peer_id(self, state: Any, runtime: Any) -> str | None:
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

    def _resolve_actor_peer_id(
        self,
        state: Any,
        runtime: Any,
    ) -> str | None:
        if self.actor_peer_resolver is None:
            return get_actor_peer_id()
        actor_peer_id = self.actor_peer_resolver(state, runtime)
        if actor_peer_id is None:
            return None
        if not isinstance(actor_peer_id, str):
            raise TypeError("OpenViking actor_peer_resolver must return a string or None")
        normalized = actor_peer_id.strip()
        if not normalized:
            raise ValueError("OpenViking actor_peer_resolver must not return an empty string")
        return normalized

    def _actor_peer_scope(
        self,
        actor_peer_id: str | None,
    ) -> AbstractContextManager[None]:
        if self.actor_peer_resolver is None:
            return nullcontext()
        return use_actor_peer(actor_peer_id)


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


def _capture_key(
    session_id: str,
    peer_id: str | None,
    actor_peer_id: str | None,
) -> _CaptureKey:
    if actor_peer_id is None:
        return (session_id, peer_id or "")
    return (actor_peer_id, session_id, peer_id or "")


def _validate_actor_peer_transport(
    *,
    actor_peer_resolver: _ActorPeerResolver | None,
    client: Any,
    async_client: Any,
    retriever: OpenVikingRetriever | None,
) -> None:
    if actor_peer_resolver is None:
        return
    require_request_actor_peer_support()
    transports = [client, async_client]
    if retriever is not None:
        transports.extend([retriever.client, retriever.async_client])
    for transport in transports:
        if transport is not None and not getattr(
            transport,
            "supports_request_actor_peer",
            False,
        ):
            raise ValueError(
                "actor_peer_resolver requires OpenViking HTTP clients that support "
                "request-scoped actor peers"
            )


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
