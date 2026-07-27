# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reusable OpenViking session recording for LangChain messages."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

try:
    from langchain_core.messages import BaseMessage
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from openviking.integrations.langchain.client import missing_dependency

    raise missing_dependency("langchain", "langchain-core") from exc

from openviking.integrations.langchain.client import (
    OpenVikingCommitPolicy,
    OpenVikingConnection,
    apply_commit_policy,
    call_openviking,
    ensure_client,
    item_value,
)
from openviking.integrations.langchain.messages import (
    is_recordable_langchain_message,
    langchain_message_to_openviking,
)

MAX_RECORDING_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class OpenVikingRecordResult:
    """Outcome of one successful recorder call."""

    messages_written: int = 0
    context_attached: bool = False


class OpenVikingSessionRecorder:
    """Persist caller-selected LangChain messages to OpenViking sessions.

    The recorder intentionally does not decide which messages form a turn or
    deduplicate transcript snapshots. Callers own that policy and pass only the
    messages they want persisted.
    """

    def __init__(
        self,
        *,
        client: Any = None,
        url: str | None = None,
        api_key: str | None = None,
        account: str | None = None,
        user: str | None = None,
        user_id: str | None = None,
        actor_peer_id: str | None = None,
        path: str | None = None,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        auto_initialize: bool = True,
        commit_policy: OpenVikingCommitPolicy | None = None,
        batch_size: int = MAX_RECORDING_BATCH_SIZE,
    ):
        if batch_size <= 0 or batch_size > MAX_RECORDING_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_RECORDING_BATCH_SIZE}")
        self._connection = OpenVikingConnection(
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            actor_peer_id=actor_peer_id,
            path=path,
            timeout=timeout,
            extra_headers=extra_headers,
            auto_initialize=auto_initialize,
        )
        self._owns_client = client is None
        self._client_cache: Any = None
        self.commit_policy = commit_policy
        self.batch_size = batch_size

    @property
    def client(self) -> Any:
        """Return the lazily initialized OpenViking client used by this recorder."""

        if self._client_cache is None:
            self._client_cache = ensure_client(self._connection)
        return self._client_cache

    def record(
        self,
        session_id: str,
        messages: Iterable[BaseMessage],
        peer_id: str | None = None,
        context_parts: Sequence[dict[str, Any]] = (),
    ) -> OpenVikingRecordResult:
        """Persist a caller-selected batch and apply the configured commit policy."""

        payloads, context_attached = _prepare_payloads(
            messages,
            peer_id=peer_id,
            context_parts=context_parts,
        )
        if not payloads:
            return OpenVikingRecordResult()

        client = self.client
        for start in range(0, len(payloads), self.batch_size):
            call_openviking(
                client,
                "batch_add_messages",
                session_id=session_id,
                messages=payloads[start : start + self.batch_size],
            )
        apply_commit_policy(client, session_id, self.commit_policy)
        return OpenVikingRecordResult(
            messages_written=len(payloads),
            context_attached=context_attached,
        )

    def flush(self, session_id: str) -> dict[str, Any] | None:
        """Commit a session only when it contains pending, uncommitted content."""

        client = self.client
        try:
            session = call_openviking(
                client,
                "get_session",
                session_id=session_id,
                auto_create=False,
            )
        except Exception as exc:
            error_code = str(getattr(exc, "code", "")).upper()
            if isinstance(exc, FileNotFoundError) or error_code == "NOT_FOUND":
                return None
            raise
        if int(item_value(session, "pending_tokens", 0) or 0) <= 0:
            return None
        return call_openviking(client, "commit_session", session_id=session_id)

    def close(self) -> None:
        """Release an internally created client.

        Injected clients remain owned by their caller and are never closed here.
        """

        client = self._client_cache
        self._client_cache = None
        if client is None or not self._owns_client:
            return
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _prepare_payloads(
    messages: Iterable[BaseMessage],
    *,
    peer_id: str | None,
    context_parts: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    normalized_peer_id = _normalize_peer_id(peer_id)
    pending_context = deepcopy(list(context_parts))
    context_attached = False
    payloads: list[dict[str, Any]] = []

    for message in messages:
        if not is_recordable_langchain_message(message):
            continue
        for raw_payload in langchain_message_to_openviking(message):
            payload = deepcopy(raw_payload)
            if pending_context and payload["role"] == "assistant":
                payload["parts"].extend(pending_context)
                pending_context = []
                context_attached = True
            if normalized_peer_id is not None:
                payload["peer_id"] = normalized_peer_id
            payloads.append(payload)
    return payloads, context_attached


def _normalize_peer_id(peer_id: str | None) -> str | None:
    if peer_id is None:
        return None
    text = str(peer_id).strip()
    return text or None
