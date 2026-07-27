# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reusable OpenViking session recording for LangChain messages."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

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
    is_context_carrier_langchain_message,
    is_recordable_langchain_message,
    langchain_message_to_openviking,
)

MAX_RECORDING_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class OpenVikingRecordResult:
    """Confirmed progress from a recorder call."""

    messages_written: int = 0
    input_messages_consumed: int = 0
    context_attached: bool = False


class OpenVikingPartialWriteError(RuntimeError):
    """Report confirmed progress when recording cannot finish."""

    def __init__(
        self,
        *,
        session_id: str,
        result: OpenVikingRecordResult,
        cause: Exception,
        stage: Literal["batch", "commit"] = "batch",
    ):
        if stage == "batch":
            detail = "before a later batch failed"
        else:
            detail = "before the commit policy failed"
        super().__init__(
            f"OpenViking recorded {result.messages_written} messages for session "
            f"{session_id!r} {detail}: {cause}"
        )
        self.session_id = session_id
        self.result = result
        self.stage = stage

    @property
    def messages_written(self) -> int:
        """Return the number of payloads confirmed written before failure."""

        return self.result.messages_written

    @property
    def input_messages_consumed(self) -> int:
        """Return the caller-message prefix that is safe to skip on retry."""

        return self.result.input_messages_consumed

    @property
    def context_attached(self) -> bool:
        """Return whether recalled context was confirmed persisted."""

        return self.result.context_attached

    @property
    def commit_pending(self) -> bool:
        """Return whether only the post-write commit remains incomplete."""

        return self.stage == "commit"


@dataclass(slots=True)
class _PreparedMessage:
    input_end: int
    payloads: list[dict[str, Any]]
    context_attached: bool = False


@dataclass(slots=True)
class _PreparedBatch:
    input_end: int
    payloads: list[dict[str, Any]]
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
        self._pending_commit_sessions: set[str] = set()
        self._closed = False
        self.commit_policy = commit_policy
        self.batch_size = batch_size

    @property
    def client(self) -> Any:
        """Return the lazily initialized OpenViking client used by this recorder."""

        self._raise_if_closed()
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

        self._raise_if_closed()
        self._retry_pending_commit(session_id)
        input_messages = list(messages)
        prepared_messages = _prepare_messages(
            input_messages,
            peer_id=peer_id,
            context_parts=context_parts,
        )
        if not prepared_messages:
            return OpenVikingRecordResult(input_messages_consumed=len(input_messages))

        batches = _prepare_batches(prepared_messages, batch_size=self.batch_size)
        client = self.client
        messages_written = 0
        input_messages_consumed = 0
        context_attached = False
        for batch in batches:
            try:
                call_openviking(
                    client,
                    "batch_add_messages",
                    session_id=session_id,
                    messages=batch.payloads,
                )
            except Exception as exc:
                if messages_written == 0:
                    raise
                result = OpenVikingRecordResult(
                    messages_written=messages_written,
                    input_messages_consumed=input_messages_consumed,
                    context_attached=context_attached,
                )
                raise OpenVikingPartialWriteError(
                    session_id=session_id,
                    result=result,
                    cause=exc,
                ) from exc
            messages_written += len(batch.payloads)
            input_messages_consumed = batch.input_end
            context_attached = context_attached or batch.context_attached
        result = OpenVikingRecordResult(
            messages_written=messages_written,
            input_messages_consumed=len(input_messages),
            context_attached=context_attached,
        )
        try:
            apply_commit_policy(client, session_id, self.commit_policy)
        except Exception as exc:
            self._pending_commit_sessions.add(session_id)
            raise OpenVikingPartialWriteError(
                session_id=session_id,
                result=result,
                cause=exc,
                stage="commit",
            ) from exc
        return result

    def flush(self, session_id: str) -> dict[str, Any] | None:
        """Commit a session only when it contains pending, uncommitted content."""

        result = self._flush_pending_content(session_id)
        self._pending_commit_sessions.discard(session_id)
        return result

    def _flush_pending_content(self, session_id: str) -> dict[str, Any] | None:
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

    def _retry_pending_commit(self, session_id: str) -> None:
        if session_id not in self._pending_commit_sessions:
            return
        self._flush_pending_content(session_id)
        self._pending_commit_sessions.discard(session_id)

    def close(self) -> None:
        """Release an internally created client.

        Injected clients remain owned by their caller and are never closed here.
        """

        if self._closed:
            return
        self._closed = True
        self._pending_commit_sessions.clear()
        client = self._client_cache
        self._client_cache = None
        if client is None or not self._owns_client:
            return
        close = getattr(client, "close", None)
        if callable(close):
            close()

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise RuntimeError("OpenVikingSessionRecorder is closed")


def _prepare_messages(
    messages: Sequence[BaseMessage],
    *,
    peer_id: str | None,
    context_parts: Sequence[dict[str, Any]],
) -> list[_PreparedMessage]:
    normalized_peer_id = _normalize_peer_id(peer_id)
    pending_context = deepcopy(list(context_parts))
    prepared_messages: list[_PreparedMessage] = []

    for index, message in enumerate(messages):
        context_carrier = is_context_carrier_langchain_message(message)
        recordable = is_recordable_langchain_message(message)
        if not recordable and not (pending_context and context_carrier):
            continue
        payloads = langchain_message_to_openviking(message)
        message_context_attached = False
        for payload in payloads:
            if pending_context and context_carrier and payload["role"] == "assistant":
                payload["parts"].extend(pending_context)
                pending_context = []
                message_context_attached = True
            if normalized_peer_id is not None:
                payload["peer_id"] = normalized_peer_id
        if payloads:
            prepared_messages.append(
                _PreparedMessage(
                    input_end=index + 1,
                    payloads=payloads,
                    context_attached=message_context_attached,
                )
            )
    return prepared_messages


def _prepare_batches(
    messages: Sequence[_PreparedMessage],
    *,
    batch_size: int,
) -> list[_PreparedBatch]:
    batches: list[_PreparedBatch] = []
    payloads: list[dict[str, Any]] = []
    input_end = 0
    context_attached = False

    for message in messages:
        payload_count = len(message.payloads)
        if payload_count > batch_size:
            raise ValueError(
                "one LangChain message produced more OpenViking payloads "
                f"({payload_count}) than batch_size ({batch_size})"
            )
        if payloads and len(payloads) + payload_count > batch_size:
            batches.append(
                _PreparedBatch(
                    input_end=input_end,
                    payloads=payloads,
                    context_attached=context_attached,
                )
            )
            payloads = []
            context_attached = False
        payloads.extend(message.payloads)
        input_end = message.input_end
        context_attached = context_attached or message.context_attached

    if payloads:
        batches.append(
            _PreparedBatch(
                input_end=input_end,
                payloads=payloads,
                context_attached=context_attached,
            )
        )
    return batches


def _normalize_peer_id(peer_id: str | None) -> str | None:
    if peer_id is None:
        return None
    text = str(peer_id).strip()
    return text or None
