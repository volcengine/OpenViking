# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Replay normalized messages into OpenViking via the SDK HTTP client.

``ConversationReplayClient`` is a thin, vikingbot-free wrapper over ``ov.AsyncHTTPClient``
(client-side, transport-agnostic: targets a local or remote server). ``SessionReplayer``
drives the canonical recipe in idempotent batches:

  reconcile() -> for each <=100-message batch: set_pending -> append -> confirm -> commit_if_needed

Each batch's intent (target cursor + size + the server's message count *before* the
append) is persisted BEFORE the append. If the process crashes mid-append, the next run
``reconcile()`` compares the server's current message count against that baseline to decide
whether the batch landed — confirming it (no re-append) or dropping the intent (re-read
from the confirmed cursor). The cursor only advances on a confirmed append, and
``needs_commit`` ensures appended-but-uncommitted sessions still get extracted later.
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
        cls, *, url: str = "", api_key: str = "", account: str = "", user: str = ""
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
        return await self._client.commit_session(ov_session_id, keep_recent_count=keep_recent_count)

    async def _session_info(self, ov_session_id: str) -> Dict[str, Any]:
        try:
            return await self._client.get_session(ov_session_id)
        except NotFoundError:
            return {}

    async def pending_tokens(self, ov_session_id: str) -> int:
        info = await self._session_info(ov_session_id)
        return int(info.get("pending_tokens", 0) or 0)

    async def message_count(self, ov_session_id: str) -> int:
        info = await self._session_info(ov_session_id)
        return int(info.get("message_count", 0) or 0)

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

    async def reconcile(self, harness: str, ref: SessionRef) -> None:
        """Resolve a pending batch intent left by a crash before the next read happens."""
        rec = self.store.get(harness, ref.native_session_id)
        if not rec or not rec.pending_count or rec.pending_cursor is None:
            return
        sid = self.ov_session_id(harness, ref.native_session_id)
        try:
            count = await self.client.message_count(sid)
        except Exception:  # noqa: BLE001 - leave the intent; retry next time
            logger.warning("[ingest:%s] reconcile read failed for %s", harness, sid)
            return
        if count >= rec.pending_baseline + rec.pending_count:
            # The batch landed before the crash: confirm it without re-appending.
            self.store.confirm_append(
                harness, ref.native_session_id, rec.pending_cursor, rec.pending_count
            )
            logger.info("[ingest:%s] reconciled landed batch for %s", harness, sid)
        else:
            # It did not land: drop the intent and re-read from the confirmed cursor.
            self.store.clear_pending(harness, ref.native_session_id)

    async def append_batch(
        self,
        harness: str,
        ref: SessionRef,
        messages: List[NormalizedMessage],
        from_cursor: Cursor,
        new_cursor: Cursor,
    ) -> int:
        """Append one <=100 batch idempotently and advance the confirmed cursor."""
        sid = self.ov_session_id(harness, ref.native_session_id)
        payloads = to_add_message_requests(messages)
        if not payloads:
            # Only control/empty turns consumed: advance the cursor, no commit owed.
            self.store.advance_cursor(
                harness, ref.native_session_id, sid, new_cursor, locator=ref.locator
            )
            return 0
        await self.client.ensure_session(sid, self.memory_policy)
        baseline = await self.client.message_count(sid)
        self.store.set_pending(
            harness,
            ref.native_session_id,
            sid,
            from_cursor,
            new_cursor,
            len(payloads),
            baseline,
            locator=ref.locator,
            title=ref.title,
        )
        await self.client.append(sid, payloads)
        self.store.confirm_append(harness, ref.native_session_id, new_cursor, len(payloads))
        return len(payloads)

    async def commit_if_needed(
        self, harness: str, ref: SessionRef, keep_recent_count: int = 0
    ) -> bool:
        """Commit when the session has un-committed appends or live pending tokens."""
        sid = self.ov_session_id(harness, ref.native_session_id)
        rec = self.store.get(harness, ref.native_session_id)
        need = bool(rec and rec.needs_commit)
        pending = await self.client.pending_tokens(sid)
        if not need and pending <= 0:
            return False
        if pending <= 0:
            # Nothing live to archive (already committed elsewhere); clear the stale flag.
            self.store.mark_committed(harness, ref.native_session_id)
            return False
        await self.client.commit(sid, keep_recent_count=keep_recent_count)
        self.store.mark_committed(harness, ref.native_session_id)
        return True

    async def maybe_commit_on_threshold(
        self, harness: str, ref: SessionRef, threshold: int, keep_recent_count: int = 0
    ) -> bool:
        sid = self.ov_session_id(harness, ref.native_session_id)
        pending = await self.client.pending_tokens(sid)
        if pending < threshold:
            return False
        await self.client.commit(sid, keep_recent_count=keep_recent_count)
        self.store.mark_committed(harness, ref.native_session_id)
        return True

    async def reset_session(self, harness: str, ref: SessionRef) -> None:
        sid = self.ov_session_id(harness, ref.native_session_id)
        await self.client.delete(sid)
        self.store.delete(harness, ref.native_session_id)
