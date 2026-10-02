# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Recall and conversation capture through MAF's public context provider interface."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Literal
from uuid import uuid4

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
    """

    after_run_once_per_turn = True

    def __init__(
        self,
        client: AsyncHTTPClient,
        *,
        source_id: str = "openviking",
        peer_scope: Literal["all", "actor"] = "all",
        limit: int = 5,
        token_budget: int = 4000,
    ) -> None:
        if not source_id or limit < 1 or not 64 <= token_budget <= 32000:
            raise ValueError("source_id must be non-empty; limit > 0; token_budget in [64, 32000]")
        if peer_scope not in ("all", "actor"):
            raise ValueError("peer_scope must be 'all' or 'actor'")
        super().__init__(source_id)
        self.client = client
        self.peer_scope = peer_scope
        self.limit = limit
        self.token_budget = token_budget
        self._write_lock = asyncio.Lock()

    def _state(self, session: AgentSession) -> dict[str, Any]:
        return session.state.setdefault(self.source_id, {})

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
        if state.get("pending_messages"):
            raise RuntimeError(
                "OpenViking capture is unconfirmed. Inspect pending_messages in provider state, "
                "then explicitly call flush(session) before continuing."
            )
        state["capture_ready"] = False
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
        """Capture the completed turn, including function calls and their results."""
        if context.response is None or not state.pop("capture_ready", False):
            return
        if state.get("pending_messages"):
            raise RuntimeError("OpenViking has pending capture; flush it before another run")
        messages = [*context.input_messages, *context.response.messages]
        recorded = set(state.get("recorded_message_ids", []))
        payloads = _messages(messages, recorded)
        if not payloads:
            return
        # Retain capture even if cancellation happens while waiting for another session.
        state["pending_messages"] = payloads
        async with self._write_lock:
            await self._flush(session)

    async def flush(self, session: AgentSession) -> None:
        """Explicitly retry pending capture, removing only acknowledged batches.

        A lost HTTP response can mean the server already appended the batch.
        Check server records before retrying: the append API is not idempotent.
        """
        async with self._write_lock:
            await self._flush(session)

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
