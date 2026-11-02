# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangGraph agent middleware for OpenViking recall and capture."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import AbstractContextManager, nullcontext
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Callable, TypedDict

try:
    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import AgentState, ModelRequest, PrivateStateAttr
    from langchain_core.messages import (
        AIMessage,
        BaseMessage,
        HumanMessage,
        ToolMessage,
    )
    from langgraph.config import get_config
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
    get_message_role,
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
_RECALL_LEDGER_MAX_ENTRIES = 500
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


class _PendingContext(TypedDict):
    delivery_id: str
    parts: list[dict[str, Any]]


class OpenVikingCaptureState(AgentState):
    # Persist delivery IDs in the host checkpointer, independently of message
    # retention, so a compacted tail is not recorded again after a restart.
    openviking_captured_message_ids: dict[str, list[str]]
    # Recall is request-only, so keep the exact block separately and replay it
    # onto the same user message when the host rebuilds model-bound history.
    openviking_recall_ledger: Annotated[dict[str, dict[str, str]], PrivateStateAttr]
    # References are pending only until their assistant message is acknowledged.
    openviking_pending_context: Annotated[dict[str, _PendingContext], PrivateStateAttr]


class OpenVikingContextMiddleware(AgentMiddleware[OpenVikingCaptureState]):
    """Inject OpenViking recall into LangGraph agent model calls.

    The middleware mirrors the OpenClaw-style lifecycle at LangGraph's extension
    points: recall before model calls and optional session capture after agent
    execution.
    """

    state_schema = OpenVikingCaptureState

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
        include_session_context: bool = False,
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
            include_session_context=include_session_context,
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
        self._captured_message_ids: dict[_CaptureKey, set[str]] = {}
        self._pending_context_parts: dict[_CaptureKey, list[dict[str, Any]]] = {}
        self._pending_recall_blocks: dict[_CaptureKey, tuple[str, str]] = {}

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
        if self._has_current_recall(request, pending_key):
            updated_request = self._request_with_replayed_context(request, pending_key)
        else:
            self._discard_pending_recall(pending_key)
            with self._actor_peer_scope(actor_peer_id):
                assembled = self.assembler.assemble(
                    session_id=session_id,
                    query=query,
                )
            updated_request = self._request_with_context(request, pending_key, assembled)
        try:
            return handler(updated_request or request)
        except BaseException:
            self._discard_pending_recall(pending_key)
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
        if self._has_current_recall(request, pending_key):
            updated_request = self._request_with_replayed_context(request, pending_key)
        else:
            self._discard_pending_recall(pending_key)
            with self._actor_peer_scope(actor_peer_id):
                assembled = await self.assembler.aassemble(
                    session_id=session_id,
                    query=query,
                )
            updated_request = self._request_with_context(request, pending_key, assembled)
        try:
            return await handler(updated_request or request)
        except BaseException:
            # CancelledError is a BaseException, so cancellation must also discard
            # context references prepared for the interrupted model call.
            self._discard_pending_recall(pending_key)
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
        return session_id, pending_key, query, actor_peer_id

    def _discard_pending_recall(self, pending_key: _CaptureKey) -> None:
        self._pending_context_parts.pop(pending_key, None)
        self._pending_recall_blocks.pop(pending_key, None)

    def _request_with_context(
        self,
        request: ModelRequest,
        pending_key: _CaptureKey,
        assembled: OpenVikingAssembledContext,
    ) -> ModelRequest | None:
        context_block = assembled.block
        if assembled.context_parts:
            self._pending_context_parts[pending_key] = assembled.context_parts
        message_key = _latest_user_recall_key(request.messages)
        if message_key is not None:
            self._pending_recall_blocks[pending_key] = (message_key, context_block)
            return self._request_with_replayed_context(request, pending_key)
        return self._request_with_replayed_context(
            request,
            pending_key,
            untracked_current_block=context_block,
        )

    def _has_current_recall(self, request: ModelRequest, pending_key: _CaptureKey) -> bool:
        message_key = _latest_user_recall_key(request.messages)
        if message_key is None:
            return False
        ledger = _recall_ledger(getattr(request, "state", {}) or {}, pending_key)
        if message_key in ledger:
            return True
        pending = self._pending_recall_blocks.get(pending_key)
        return pending is not None and pending[0] == message_key

    def _request_with_replayed_context(
        self,
        request: ModelRequest,
        pending_key: _CaptureKey,
        *,
        untracked_current_block: str | None = None,
    ) -> ModelRequest | None:
        messages = list(request.messages)
        latest_user_index = _latest_user_index(messages)
        if latest_user_index is None:
            return None
        ledger = _recall_ledger(getattr(request, "state", {}) or {}, pending_key)
        pending = self._pending_recall_blocks.get(pending_key)
        if pending is not None:
            ledger[pending[0]] = pending[1]

        changed = False
        for index, message in enumerate(messages):
            if get_message_role(message) != "user":
                continue
            message_key = _recall_message_key(message)
            if message_key is not None and message_key in ledger:
                block = ledger[message_key]
            elif index == latest_user_index and untracked_current_block is not None:
                block = untracked_current_block
            else:
                continue
            if not block:
                continue
            messages[index] = _message_with_recall(message, block)
            changed = True
        return request.override(messages=messages) if changed else None

    def before_model(self, state: OpenVikingCaptureState, runtime: Any) -> dict[str, Any] | None:
        """Place this middleware before the host summarizer to capture its input."""
        return self._capture(state, runtime, commit=False)

    def after_model(
        self,
        state: OpenVikingCaptureState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        return self._save_pending_recall(state, runtime)

    async def abefore_model(
        self, state: OpenVikingCaptureState, runtime: Any
    ) -> dict[str, Any] | None:
        return await self._acapture(state, runtime, commit=False)

    async def aafter_model(
        self,
        state: OpenVikingCaptureState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        return self._save_pending_recall(state, runtime)

    def _save_pending_recall(
        self,
        state: OpenVikingCaptureState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        if not self._pending_recall_blocks:
            return None
        pending_key = _capture_key(
            self._resolve_session_id(state, runtime),
            self._resolve_peer_id(state, runtime),
            self._resolve_actor_peer_id(state, runtime),
        )
        pending = self._pending_recall_blocks.pop(pending_key, None)
        if pending is None:
            return None
        message_key, block = pending
        ledgers = dict(state.get("openviking_recall_ledger") or {})
        scope_key = _stable_json(pending_key)
        ledger = dict(ledgers.get(scope_key) or {})
        update: dict[str, Any] = {}
        if ledger.get(message_key) != block:
            ledger[message_key] = block
            while len(ledger) > _RECALL_LEDGER_MAX_ENTRIES:
                del ledger[next(iter(ledger))]
            ledgers[scope_key] = ledger
            update["openviking_recall_ledger"] = ledgers

        parts = self._pending_context_parts.get(pending_key)
        messages = state.get("messages") or []
        delivery_id = _message_delivery_id(messages[-1]) if messages else None
        if parts and delivery_id:
            pending_context = dict(state.get("openviking_pending_context") or {})
            pending_context[scope_key] = {
                "delivery_id": delivery_id,
                "parts": deepcopy(parts),
            }
            update["openviking_pending_context"] = pending_context
        return update or None

    def _capture_state_update(
        self, state: OpenVikingCaptureState, plan: _CapturePlan
    ) -> dict[str, Any]:
        delivered = dict(state.get("openviking_captured_message_ids") or {})
        delivered[_stable_json(plan.key)] = sorted(self._captured_message_ids.get(plan.key, set()))
        update: dict[str, Any] = {"openviking_captured_message_ids": delivered}
        pending_context = state.get("openviking_pending_context") or {}
        scope_key = _stable_json(plan.key)
        pending = pending_context.get(scope_key)
        if pending and pending["delivery_id"] in self._captured_message_ids.get(plan.key, set()):
            pending_context = dict(pending_context)
            del pending_context[scope_key]
            update["openviking_pending_context"] = pending_context
        return {key: value for key, value in update.items() if state.get(key) != value}

    def _remember_capture_ids(self, plan: _CapturePlan, end: int | None = None) -> None:
        delivered = self._captured_message_ids.setdefault(plan.key, set())
        for message in plan.messages[:end]:
            message_id = _message_delivery_id(message)
            if message_id:
                delivered.add(message_id)

    def after_agent(self, state: OpenVikingCaptureState, runtime: Any) -> dict[str, Any] | None:
        return self._capture(state, runtime, commit=True)

    def _capture(
        self, state: OpenVikingCaptureState, runtime: Any, *, commit: bool
    ) -> dict[str, Any] | None:
        plan = self._capture_plan(state, runtime)
        if plan is None:
            return None
        policy = self.commit_policy if commit else None
        if plan.unchanged:
            with self._actor_peer_scope(plan.actor_peer_id):
                self.recorder.record(plan.session_id, (), commit_policy=policy)
            self._pending_context_parts.pop(plan.key, None)
            return self._capture_state_update(state, plan) or None
        try:
            with self._actor_peer_scope(plan.actor_peer_id):
                result = self.recorder.record(
                    plan.session_id,
                    plan.messages[plan.start :],
                    peer_id=plan.peer_id,
                    context_parts=plan.context_parts,
                    commit_policy=policy,
                )
        except OpenVikingPartialWriteError as exc:
            self._handle_partial_capture(plan, exc)
            raise

        self._complete_capture(plan, context_attached=result.context_attached)
        return self._capture_state_update(state, plan)

    async def aafter_agent(
        self,
        state: OpenVikingCaptureState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Asynchronously capture messages after an agent run."""
        return await self._acapture(state, runtime, commit=True)

    async def _acapture(
        self, state: OpenVikingCaptureState, runtime: Any, *, commit: bool
    ) -> dict[str, Any] | None:
        plan = self._capture_plan(state, runtime)
        if plan is None:
            return None
        policy = self.commit_policy if commit else None
        if plan.unchanged:
            with self._actor_peer_scope(plan.actor_peer_id):
                await self.recorder.arecord(plan.session_id, (), commit_policy=policy)
            self._pending_context_parts.pop(plan.key, None)
            return self._capture_state_update(state, plan) or None
        try:
            with self._actor_peer_scope(plan.actor_peer_id):
                result = await self.recorder.arecord(
                    plan.session_id,
                    plan.messages[plan.start :],
                    peer_id=plan.peer_id,
                    context_parts=plan.context_parts,
                    commit_policy=policy,
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
        return self._capture_state_update(state, plan)

    def _capture_plan(
        self,
        state: OpenVikingCaptureState,
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
        delivered = self._captured_message_ids.setdefault(key, set())
        delivered.update(
            (state.get("openviking_captured_message_ids") or {}).get(_stable_json(key), [])
        )
        messages = [
            message
            for message in messages
            if _message_delivery_id(message) not in delivered and not _is_host_summary(message)
        ]
        previous_signatures = self._captured_signatures.get(key, ())
        signatures = tuple(_message_signature(message) for message in messages)
        unchanged = not messages or signatures == previous_signatures
        start = 0
        if (
            not unchanged
            and previous_signatures
            and len(signatures) > len(previous_signatures)
            and signatures[: len(previous_signatures)] == previous_signatures
        ):
            start = len(previous_signatures)
        context_parts = list(self._pending_context_parts.get(key, []))
        if not context_parts:
            # Restore references only for their still-uncaptured assistant message.
            pending = (state.get("openviking_pending_context") or {}).get(_stable_json(key))
            if pending and any(
                _message_delivery_id(message) == pending["delivery_id"] for message in messages
            ):
                context_parts = list(pending["parts"])
        return _CapturePlan(
            session_id=session_id,
            peer_id=peer_id,
            actor_peer_id=actor_peer_id,
            key=key,
            messages=messages,
            signatures=signatures,
            start=start,
            context_parts=context_parts,
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
            self._remember_capture_ids(plan, consumed_end)
        if error.context_attached:
            self._pending_context_parts.pop(plan.key, None)

    def _complete_capture(
        self,
        plan: _CapturePlan,
        *,
        context_attached: bool,
    ) -> None:
        self._captured_signatures[plan.key] = plan.signatures
        self._remember_capture_ids(plan)
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
        # The framework Runtime does not expose config; its runnable context does.
        try:
            config = get_config()
        except RuntimeError:
            config = {}
        resolved = _normalize_session_id(_nested_get(config, "configurable", "thread_id"))
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


def _is_host_summary(message: Any) -> bool:
    metadata = (
        message.get("additional_kwargs", {})
        if isinstance(message, dict)
        else getattr(message, "additional_kwargs", {})
    )
    return metadata.get("lc_source") == "summarization"


def _message_stable_id(message: Any) -> str | None:
    if isinstance(message, dict):
        value = message.get("id")
    else:
        value = getattr(message, "id", None)
    return str(value) if value else None


def _latest_user_index(messages: list[Any]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if get_message_role(messages[index]) == "user":
            return index
    return None


def _recall_message_key(message: Any) -> str | None:
    if get_message_role(message) != "user":
        return None
    message_id = _message_stable_id(message)
    if message_id is None:
        return None
    digest = hashlib.sha256(_message_content(message).encode()).hexdigest()
    return f"{message_id}:{digest}"


def _latest_user_recall_key(messages: list[Any]) -> str | None:
    index = _latest_user_index(messages)
    return None if index is None else _recall_message_key(messages[index])


def _recall_ledger(
    state: dict[str, Any],
    pending_key: _CaptureKey,
) -> dict[str, str]:
    ledgers = state.get("openviking_recall_ledger") or {}
    if not isinstance(ledgers, dict):
        return {}
    raw = ledgers.get(_stable_json(pending_key)) or {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: block for key, block in raw.items() if isinstance(key, str) and isinstance(block, str)
    }


def _message_with_recall(message: Any, block: str) -> Any:
    content = message.get("content", "") if isinstance(message, dict) else message.content
    if isinstance(content, list):
        updated_content: Any = [{"type": "text", "text": block}, *content]
    else:
        text = content if isinstance(content, str) else extract_message_text(content)
        updated_content = f"{block}\n\n{text}" if text else block
    if isinstance(message, dict):
        return {**message, "content": updated_content}
    return message.model_copy(update={"content": updated_content})


def _message_delivery_id(message: Any) -> str | None:
    if not _message_stable_id(message):
        return None
    # Editing a message with the same ID is a new capture; an unchanged retained
    # tail must remain deduplicated across host compaction and process restarts.
    return hashlib.sha256(_message_signature(message).encode()).hexdigest()


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
