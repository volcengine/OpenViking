# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LangChain chat history backed by OpenViking sessions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Callable

try:
    from langchain_core.chat_history import BaseChatMessageHistory
    from langchain_core.messages import BaseMessage
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from langchain_openviking.client import missing_dependency

    raise missing_dependency("langchain", "langchain-core") from exc

from langchain_openviking.client import (
    OpenVikingCommitPolicy,
    acall_openviking,
    call_openviking,
    is_not_found_error,
)
from langchain_openviking.messages import (
    langchain_message_to_openviking as langchain_message_to_openviking,
)
from langchain_openviking.messages import (
    openviking_message_to_langchain as openviking_message_to_langchain,
)
from langchain_openviking.messages import (
    restore_openviking_messages,
)
from langchain_openviking.recording import (
    OpenVikingPartialWriteError,
    OpenVikingSessionRecorder,
    get_openviking_cancellation_progress,
)

logger = logging.getLogger(__name__)


class OpenVikingChatMessageHistory(BaseChatMessageHistory):
    """LangChain chat history implementation stored in an OpenViking session."""

    def __init__(
        self,
        session_id: str,
        *,
        client: Any = None,
        async_client: Any = None,
        url: str | None = None,
        api_key: str | None = None,
        account: str | None = None,
        user: str | None = None,
        user_id: str | None = None,
        actor_peer_id: str | None = None,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        auto_initialize: bool = True,
        token_budget: int = 128_000,
        persist_system_messages: bool = False,
        commit_policy: OpenVikingCommitPolicy | None = None,
        context_parts_provider: Callable[[str], list[dict[str, Any]]] | None = None,
        context_parts_acknowledger: Callable[[str], None] | None = None,
        peer_id: str | None = None,
        peer_id_provider: Callable[[str], str | None] | None = None,
        _recorder: OpenVikingSessionRecorder | None = None,
    ):
        self.session_id = session_id
        self.peer_id = peer_id
        self.peer_id_provider = peer_id_provider
        self.token_budget = token_budget
        # Retained for constructor compatibility. System messages are runtime
        # policy, not conversation memory, so they are never persisted.
        self.persist_system_messages = False
        self.context_parts_provider = context_parts_provider
        self.context_parts_acknowledger = context_parts_acknowledger
        self._pending_context_parts: list[dict[str, Any]] = []
        self._owns_recorder = _recorder is None
        self._recorder = _recorder or OpenVikingSessionRecorder(
            client=client,
            async_client=async_client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            actor_peer_id=actor_peer_id,
            timeout=timeout,
            extra_headers=extra_headers,
            auto_initialize=auto_initialize,
            commit_policy=commit_policy,
        )

    @property
    def commit_policy(self) -> OpenVikingCommitPolicy | None:
        """Return the recorder commit policy used by this history."""

        return self._recorder.commit_policy

    @commit_policy.setter
    def commit_policy(self, value: OpenVikingCommitPolicy | None) -> None:
        self._recorder.commit_policy = value

    @property
    def messages(self) -> list[BaseMessage]:  # type: ignore[override]
        client = self._get_client()
        try:
            context = call_openviking(
                client,
                "get_session_context",
                session_id=self.session_id,
                token_budget=self.token_budget,
            )
        except Exception as exc:
            logger.debug("OpenViking chat history context fetch failed", exc_info=True)
            if is_not_found_error(exc):
                self._ensure_session(client)
            return []

        return restore_openviking_messages(context.get("messages") or [])

    async def aget_messages(self) -> list[BaseMessage]:
        """Asynchronously return active messages from the OpenViking session."""

        client = await self._get_async_client()
        try:
            context = await acall_openviking(
                client,
                "get_session_context",
                session_id=self.session_id,
                token_budget=self.token_budget,
            )
        except Exception as exc:
            logger.debug("OpenViking chat history context fetch failed", exc_info=True)
            if is_not_found_error(exc):
                await self._aensure_session(client)
            return []

        return restore_openviking_messages(context.get("messages") or [])

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        if not self._pending_context_parts and self.context_parts_provider:
            self._pending_context_parts = list(self.context_parts_provider(self.session_id))
        try:
            result = self._recorder.record(
                self.session_id,
                messages,
                peer_id=self._effective_peer_id(),
                context_parts=self._pending_context_parts,
            )
        except OpenVikingPartialWriteError as exc:
            if exc.context_attached:
                self._acknowledge_context_parts()
            raise
        if result.context_attached:
            self._acknowledge_context_parts()

    async def aadd_messages(self, messages: Sequence[BaseMessage]) -> None:
        """Asynchronously persist messages through the shared session recorder."""

        if not self._pending_context_parts and self.context_parts_provider:
            self._pending_context_parts = list(self.context_parts_provider(self.session_id))
        try:
            result = await self._recorder.arecord(
                self.session_id,
                messages,
                peer_id=self._effective_peer_id(),
                context_parts=self._pending_context_parts,
            )
        except OpenVikingPartialWriteError as exc:
            if exc.context_attached:
                self._acknowledge_context_parts()
            raise
        except asyncio.CancelledError as exc:
            progress = get_openviking_cancellation_progress(exc)
            if progress is not None and progress.context_attached:
                self._acknowledge_context_parts()
            raise
        if result.context_attached:
            self._acknowledge_context_parts()

    def clear(self) -> None:
        client = self._get_client()
        call_openviking(client, "delete_session", session_id=self.session_id)
        self._ensure_session(client)
        self._acknowledge_context_parts()

    async def aclear(self) -> None:
        """Asynchronously reset this OpenViking session."""

        client = await self._get_async_client()
        await acall_openviking(client, "delete_session", session_id=self.session_id)
        await self._aensure_session(client)
        self._acknowledge_context_parts()

    def close(self) -> None:
        """Release resources owned by this history adapter."""

        self._acknowledge_context_parts()
        if self._owns_recorder:
            self._recorder.close()

    async def aclose(self) -> None:
        """Asynchronously release resources owned by this history adapter."""

        self._acknowledge_context_parts()
        if self._owns_recorder:
            await self._recorder.aclose()

    def _get_client(self) -> Any:
        return self._recorder.client

    async def _get_async_client(self) -> Any:
        return await self._recorder.get_async_client()

    def _ensure_session(self, client: Any) -> None:
        try:
            call_openviking(client, "create_session", session_id=self.session_id)
        except Exception:
            logger.debug("OpenViking chat history session ensure failed", exc_info=True)
            pass

    async def _aensure_session(self, client: Any) -> None:
        try:
            await acall_openviking(client, "create_session", session_id=self.session_id)
        except Exception:
            logger.debug("OpenViking chat history session ensure failed", exc_info=True)

    def _effective_peer_id(self) -> str | None:
        if self.peer_id_provider is None:
            value = self.peer_id
        else:
            value = self.peer_id_provider(self.session_id)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _prepare_invocation(
        self,
        *,
        peer_id: str | None,
        context_parts: Sequence[dict[str, Any]] = (),
    ) -> None:
        """Set invocation-local attribution and recalled context."""

        self.peer_id = peer_id
        self._pending_context_parts = list(context_parts)

    def _discard_invocation_context(self) -> None:
        """Discard recalled context owned by an interrupted invocation."""

        self._pending_context_parts = []

    def _acknowledge_context_parts(self) -> None:
        had_pending_context = bool(self._pending_context_parts)
        self._pending_context_parts = []
        if not had_pending_context or self.context_parts_acknowledger is None:
            return
        try:
            self.context_parts_acknowledger(self.session_id)
        except Exception:
            logger.warning(
                "OpenViking context-parts acknowledgement failed",
                exc_info=True,
            )


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
