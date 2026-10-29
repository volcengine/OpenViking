# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: MIT
"""Recall and conversation capture through MAF's public context provider interface."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Literal
from uuid import uuid4
from weakref import WeakKeyDictionary

from agent_framework import (
    AgentSession,
    ChatContext,
    ChatMiddleware,
    ChatResponseUpdate,
    ContextProvider,
    Message,
    ResponseStream,
    SessionContext,
    SupportsAgentRun,
)
from openviking_sdk import AsyncHTTPClient


class _CaptureCompletion(ChatMiddleware):
    """Distinguish stream exhaustion from MAF's early-close finalization."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        self.state["capture_ready"] = False
        await call_next()
        if not isinstance(context.result, ResponseStream):
            self.state["capture_ready"] = True
            return
        original = context.result

        async def updates() -> AsyncIterator[ChatResponseUpdate]:
            async with original:
                async for update in original:
                    yield update
            self.state["capture_ready"] = True

        context.result = ResponseStream(
            updates(), finalizer=lambda _: original.get_final_response()
        )


class OpenVikingContextProvider(ContextProvider):
    """Attach OpenViking memory to an agent using an application-owned HTTP client.

    Use one provider per authenticated user and actor. Serialize runs for each MAF
    session. Different sessions may share a provider on the same asyncio event loop.
    The caller initializes and closes the client and persists MAF session state.
    Capture defaults to enabled; commits default to explicit application requests.
    Automatic policies run after acknowledged capture and never wait for extraction.
    """

    # Capture each completed agent run: loop-level streamed responses only contain
    # the final iteration in MAF, while per-run responses retain all tool evidence.
    after_run_once_per_turn = False

    def __init__(
        self,
        client: AsyncHTTPClient,
        *,
        source_id: str = "openviking",
        peer_scope: Literal["all", "actor"] = "all",
        limit: int = 5,
        token_budget: int = 4000,
        capture: bool = True,
        commit_policy: Literal["never", "always", "pending_tokens"] = "never",
        commit_token_threshold: int = 20_000,
    ) -> None:
        if not source_id or limit < 1 or not 64 <= token_budget <= 32000:
            raise ValueError("source_id must be non-empty; limit > 0; token_budget in [64, 32000]")
        if peer_scope not in ("all", "actor"):
            raise ValueError("peer_scope must be 'all' or 'actor'")
        if not isinstance(capture, bool):
            raise ValueError("capture must be a boolean")
        if commit_policy not in ("never", "always", "pending_tokens"):
            raise ValueError("commit_policy must be 'never', 'always', or 'pending_tokens'")
        if type(commit_token_threshold) is not int or commit_token_threshold < 1:
            raise ValueError("commit_token_threshold must be a positive integer")
        super().__init__(source_id)
        self.client = client
        self.peer_scope = peer_scope
        self.limit = limit
        self.token_budget = token_budget
        self.capture = capture
        self.commit_policy = commit_policy
        self.commit_token_threshold = commit_token_threshold
        self._write_lock = asyncio.Lock()
        self._session_states: WeakKeyDictionary[AgentSession, dict[str, Any]] = WeakKeyDictionary()

    def _state(self, session: AgentSession) -> dict[str, Any]:
        state = session.state.setdefault(self.source_id, {})
        # A loop's fresh_context mode rolls provider state back between iterations.
        # External appends cannot be rolled back with it; fail before another write.
        previous = self._session_states.get(session)
        if previous is not None and previous != state:
            raise RuntimeError(
                "OpenViking provider state was reset during this session. "
                "State-resetting middleware such as fresh_context=True is unsupported."
            )
        self._session_states[session] = state
        return state

    async def _ensure_session(self, session: AgentSession) -> str:
        state = self._state(session)
        if "session_id" not in state:
            # Retain the proposed ID even if the create acknowledgement is lost.
            state["session_id"] = f"maf-{uuid4().hex}"
        session_id: str = state["session_id"]
        await self.client.get_session(session_id, auto_create=True)
        return session_id

    async def before_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        """Retrieve context before inference; never replay unconfirmed writes implicitly."""
        self._state(session)
        if state.get("pending_messages"):
            raise RuntimeError(
                "OpenViking capture is unconfirmed. Inspect server records and pending_messages "
                "in provider state before explicitly calling flush(session); "
                "a lost acknowledgement may mean the messages were already appended."
            )
        state["capture_ready"] = False
        if self.capture:
            context.extend_middleware(self.source_id, [_CaptureCompletion(state)])
            for message in context.input_messages:
                if message.message_id is None:
                    message.message_id = uuid4().hex
        query = "\n".join(
            message.text
            for message in context.input_messages
            if message.role == "user" and not message.additional_properties.get("_attribution")
        )
        if not query.strip():
            return
        session_id = await self._ensure_session(session)
        result = await self.client.search_context(
            query=query,
            session_id=session_id,
            limit=self.limit,
            options={"max_tokens": self.token_budget, "peer_scope": self.peer_scope},
        )
        rendered = result.get("rendered", "")
        if rendered and result.get("entries"):
            # Memory is data from an external source, never system/developer instructions.
            context.extend_messages(
                self,
                [Message("user", ["Retrieved OpenViking context (reference data):\n" + rendered])],
            )

    async def after_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        """Capture the completed run and apply its commit policy after acknowledgement."""
        if not self.capture or context.response is None or not state.pop("capture_ready", False):
            return
        if state.get("pending_messages"):
            raise RuntimeError("OpenViking has pending capture; flush it before another run")
        messages = [*context.input_messages, *context.response.messages]
        recorded = set(state.get("recorded_message_ids", []))
        payloads = _messages(messages, recorded)
        if not payloads and not recorded:
            return
        # Retain capture even if cancellation happens while waiting for another session.
        if payloads:
            state["pending_messages"] = payloads
        async with self._write_lock:
            await self._flush(session)
            await self._maybe_commit(session)

    async def flush(self, session: AgentSession) -> None:
        """Explicitly retry pending capture, removing only acknowledged batches.

        A lost HTTP response can mean the server already appended the batch.
        Check server records before retrying: the append API is not idempotent.
        Recovered capture applies the automatic policy when capture is enabled.
        """
        async with self._write_lock:
            had_pending = bool(self._state(session).get("pending_messages"))
            await self._flush(session)
            if had_pending and self.capture:
                await self._maybe_commit(session)

    async def _flush(self, session: AgentSession) -> None:
        state = self._state(session)
        pending = state.get("pending_messages", [])
        if not pending:
            return
        session_id = await self._ensure_session(session)
        while pending:
            batch = pending[:100]
            result = await self.client.batch_add_messages(session_id, batch)
            if result.get("added") != len(batch):
                raise RuntimeError("OpenViking did not acknowledge the complete message batch")
            state.setdefault("recorded_message_ids", []).extend(
                message["source_message_ids"][0] for message in batch
            )
            del pending[: len(batch)]

    async def _maybe_commit(self, session: AgentSession) -> None:
        """Apply the policy under the write lock, after capture is acknowledged."""
        if self.commit_policy == "never":
            return
        session_id = self._state(session)["session_id"]
        if self.commit_policy == "pending_tokens":
            metadata = await self.client.get_session(session_id)
            if int(metadata.get("pending_tokens") or 0) < self.commit_token_threshold:
                return
        # Do not call commit(): this path already owns the non-reentrant lock.
        await self.client.commit_session(session_id)

    async def commit(self, session: AgentSession) -> dict[str, Any]:
        """Request extraction after capture; task acceptance is not completion."""
        async with self._write_lock:
            if self._state(session).get("pending_messages"):
                raise RuntimeError("Resolve pending OpenViking capture before committing")
            session_id = await self._ensure_session(session)
            return await self.client.commit_session(session_id)


def _messages(messages: list[Message], recorded: set[str]) -> list[dict[str, Any]]:
    """Translate native messages without recording injected context or hidden reasoning."""
    calls = {
        content.call_id: content
        for message in messages
        for content in message.contents
        if content.type == "function_call"
    }
    payloads = []
    for message in messages:
        if message.role not in ("user", "assistant", "tool"):
            continue
        if message.additional_properties.get("_attribution"):
            continue
        message_id = message.message_id or uuid4().hex
        message.message_id = message_id
        if message_id in recorded:
            continue
        parts: list[dict[str, Any]] = []
        for content in message.contents:
            if content.type == "text" and content.text:
                parts.append({"type": "text", "text": content.text})
            elif content.type in ("function_call", "function_result"):
                call = content if content.type == "function_call" else calls.get(content.call_id)
                arguments = call.arguments if call else None
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {"raw_arguments": arguments}
                is_result = content.type == "function_result"
                output = content.result
                parts.append(
                    {
                        "type": "tool",
                        "tool_id": content.call_id or "",
                        "tool_name": (call.name if call else content.name) or "",
                        "tool_input": dict(arguments)
                        if isinstance(arguments, Mapping)
                        else ({"raw_arguments": arguments} if arguments is not None else None),
                        "tool_output": (
                            output if isinstance(output, str) else json.dumps(output, default=str)
                        )
                        if is_result
                        else "",
                        "tool_status": ("error" if content.exception else "completed")
                        if is_result
                        else "pending",
                    }
                )
        if parts:
            payloads.append(
                {
                    "role": "user" if message.role == "tool" else message.role,
                    "parts": parts,
                    "source_message_ids": [message_id],
                    "message_kind": "tool_transport"
                    if message.role == "tool"
                    else ("user_query" if message.role == "user" else "assistant_step"),
                }
            )
            recorded.add(message_id)
    return payloads
