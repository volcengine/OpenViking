# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Replay normalized messages into OpenViking via the SDK HTTP client.

``ConversationReplayClient`` is a thin, vikingbot-free wrapper over ``ov.AsyncHTTPClient``
(client-side, transport-agnostic: targets a local or remote server). ``SessionReplayer``
drives the canonical recipe: ensure_session -> batch_add (<=100) -> commit, persisting the
read-cursor BEFORE commit for crash-safe, idempotent resume.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import openviking as ov
from openviking.ingest.cursor_store import CursorStore
from openviking.ingest.models import Cursor, NormalizedMessage, SessionRef
from openviking.ingest.normalize import to_add_message_requests
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_BATCH = 100  # server-side cap for batch_add_messages
_SID_ALLOWED = re.compile(r"[^a-zA-Z0-9_.@-]+")


def _sanitize_id(value: str) -> str:
    cleaned = _SID_ALLOWED.sub("-", (value or "").strip())
    return re.sub(r"-{2,}", "-", cleaned).strip("-.") or "unknown"


class ConversationReplayClient:
    """Minimal session-recording surface over the OV SDK HTTP client."""

    def __init__(self, client: "ov.AsyncHTTPClient"):
        self._client = client

    @classmethod
    async def create(
        cls,
        *,
        url: str = "",
        api_key: str = "",
        account: str = "default",
        user: str = "default",
    ) -> "ConversationReplayClient":
        client = ov.AsyncHTTPClient(
            url=url or None,
            api_key=api_key or None,
            account=account or None,
            user=user or None,
        )
        await client.initialize()
        return cls(client)

    async def close(self) -> None:
        await self._client.close()

    async def ensure_session(
        self, ov_session_id: str, memory_policy: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        try:
            return await self._client.get_session(ov_session_id)
        except NotFoundError:
            return await self._client.create_session(
                session_id=ov_session_id, memory_policy=memory_policy or None
            )

    async def append(self, ov_session_id: str, messages: List[Dict[str, Any]]) -> int:
        total = 0
        for start in range(0, len(messages), _BATCH):
            chunk = messages[start : start + _BATCH]
            result = await self._client.batch_add_messages(ov_session_id, chunk)
            total += int(result.get("added", len(chunk)) or 0)
        return total

    async def commit(self, ov_session_id: str, keep_recent_count: int = 0) -> Dict[str, Any]:
        return await self._client.commit_session(
            ov_session_id, keep_recent_count=keep_recent_count
        )

    async def pending_tokens(self, ov_session_id: str) -> int:
        try:
            info = await self._client.get_session(ov_session_id)
        except NotFoundError:
            return 0
        return int(info.get("pending_tokens", 0) or 0)

    async def delete(self, ov_session_id: str) -> None:
        try:
            await self._client.delete_session(ov_session_id)
        except NotFoundError:
            pass


class SessionReplayer:
    def __init__(
        self,
        client: ConversationReplayClient,
        store: CursorStore,
        *,
        session_id_prefix: str = "import",
        memory_policy: Optional[Dict[str, Any]] = None,
    ):
        self.client = client
        self.store = store
        self.prefix = session_id_prefix
        self.memory_policy = memory_policy or None

    def ov_session_id(self, harness: str, native_session_id: str) -> str:
        return "__".join(
            [_sanitize_id(self.prefix), _sanitize_id(harness), _sanitize_id(native_session_id)]
        )

    async def append_session(
        self,
        harness: str,
        ref: SessionRef,
        messages: List[NormalizedMessage],
        new_cursor: Cursor,
    ) -> int:
        """Ensure session, append messages, persist the advanced cursor (pre-commit)."""
        sid = self.ov_session_id(harness, ref.native_session_id)
        payloads = to_add_message_requests(messages)
        added = 0
        if payloads:
            await self.client.ensure_session(sid, self.memory_policy)
            added = await self.client.append(sid, payloads)
        # Persist cursor even if everything was filtered out, so we don't re-scan it.
        self.store.upsert(
            harness,
            ref.native_session_id,
            sid,
            new_cursor,
            appended_delta=added,
            locator=ref.locator,
            title=ref.title,
        )
        return added

    async def commit_session(self, harness: str, ref: SessionRef, keep_recent_count: int = 0) -> bool:
        """Commit if the session has pending (un-archived) tokens. Returns whether committed."""
        sid = self.ov_session_id(harness, ref.native_session_id)
        pending = await self.client.pending_tokens(sid)
        if pending <= 0:
            return False
        await self.client.commit(sid, keep_recent_count=keep_recent_count)
        rec = self.store.get(harness, ref.native_session_id)
        if rec is not None:
            self.store.upsert(
                harness,
                ref.native_session_id,
                sid,
                rec.cursor,
                pending_tokens=0,
                committed=True,
            )
        return True

    async def maybe_commit_on_threshold(
        self, harness: str, ref: SessionRef, threshold: int, keep_recent_count: int = 0
    ) -> bool:
        """Commit only if pending tokens reached the threshold (incremental/watch mode)."""
        sid = self.ov_session_id(harness, ref.native_session_id)
        pending = await self.client.pending_tokens(sid)
        if pending < threshold:
            return False
        await self.client.commit(sid, keep_recent_count=keep_recent_count)
        rec = self.store.get(harness, ref.native_session_id)
        if rec is not None:
            self.store.upsert(
                harness, ref.native_session_id, sid, rec.cursor, pending_tokens=0, committed=True
            )
        return True

    async def reset_session(self, harness: str, ref: SessionRef) -> None:
        """Delete the OV session and local cursor (for --reset)."""
        sid = self.ov_session_id(harness, ref.native_session_id)
        await self.client.delete(sid)
        self.store.delete(harness, ref.native_session_id)
