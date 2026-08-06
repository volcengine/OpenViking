# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cross-turn dedup ledger.

Recording served URIs on the server means every harness plugin inherits
cross-turn dedup for free, and the server knows which tier each URI was served
at. The ledger is bookkeeping: it is written raw so it never enters semantic
processing, and any failure degrades to "no dedup" rather than failing recall.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional, Set

from openviking.server.identity import RequestContext
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

LEDGER_FILENAME = ".recall_log.json"
LEDGER_VERSION = 1
MAX_LEDGER_URIS = 500


def _record_turn(record: Any) -> Optional[int]:
    """Turn a stored record was written at, or ``None`` when it is unusable.

    The ledger is a file on disk: a half-written or hand-edited record must
    degrade to "no dedup for that URI", never fail the request that reads it.
    """
    if not isinstance(record, dict):
        return None
    try:
        return int(record.get("turn", 0))
    except (TypeError, ValueError):
        return None


def _resolve_turn(session: Any) -> int:
    """Monotonic message counter used as the turn clock."""
    meta = getattr(session, "meta", None)
    total = getattr(meta, "total_message_count", None) if meta is not None else None
    live = len(getattr(session, "messages", None) or [])
    try:
        total_int = int(total) if total is not None else 0
    except (TypeError, ValueError):
        total_int = 0
    return max(total_int, live)


class RecallLedger:
    """Turn-distance cooldown over URIs already served in this session."""

    def __init__(
        self,
        *,
        service: Any,
        ctx: RequestContext,
        session_uri: str,
        turn: int,
        dedup_turns: int,
        data: Optional[Dict[str, Any]] = None,
        status: str = "ok",
    ) -> None:
        self._service = service
        self._ctx = ctx
        self._uri = f"{session_uri.rstrip('/')}/{LEDGER_FILENAME}"
        self._turn = turn
        self._dedup_turns = max(0, int(dedup_turns))
        self._data = data or {}
        self.status = status

    @classmethod
    async def load(
        cls,
        *,
        service: Any,
        ctx: RequestContext,
        session: Any,
        dedup_turns: int,
    ) -> Optional["RecallLedger"]:
        """Load the ledger, or return ``None`` when dedup cannot apply."""
        if dedup_turns <= 0 or session is None:
            return None
        session_uri = str(getattr(session, "uri", "") or "")
        viking_fs = getattr(service, "viking_fs", None)
        if not session_uri or viking_fs is None:
            return None

        ledger = cls(
            service=service,
            ctx=ctx,
            session_uri=session_uri,
            turn=_resolve_turn(session),
            dedup_turns=dedup_turns,
        )
        try:
            raw = await viking_fs.read_file(ledger._uri, ctx=ctx)
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                ledger._data = parsed
            ledger.status = "ok"
        except Exception as exc:
            logger.debug("Recall ledger unreadable (%s); starting empty", exc)
            ledger._data = {}
            ledger.status = "new"
        return ledger

    @property
    def turn(self) -> int:
        return self._turn

    def _entries(self) -> Dict[str, Any]:
        entries = self._data.get("entries")
        return entries if isinstance(entries, dict) else {}

    def cooled_uris(self) -> Set[str]:
        """URIs served within the last ``dedup_turns`` turns."""
        cooled: Set[str] = set()
        for uri, record in self._entries().items():
            served_turn = _record_turn(record)
            if served_turn is None or served_turn > self._turn:
                # Archive rotation reset the clock; treat the record as expired.
                continue
            if record.get("detail") == "uri":
                # A bare URI carried no content, so the next turn is free to
                # serve it again — the entry lost to budget pressure, not to
                # the reader having already seen it.
                continue
            if self._turn - served_turn < self._dedup_turns:
                cooled.add(str(uri))
        return cooled

    async def record(self, entries: Iterable[Any]) -> None:
        """Upsert served URIs at the current turn; failures are non-fatal."""
        viking_fs = getattr(self._service, "viking_fs", None)
        if viking_fs is None:
            return
        stored = dict(self._entries())
        for entry in entries:
            uri = str(getattr(entry, "uri", "") or "")
            if not uri:
                continue
            stored[uri] = {"turn": self._turn, "detail": getattr(entry, "detail", "")}

        keep_from = self._turn - (self._dedup_turns * 4)
        turns = {uri: _record_turn(record) for uri, record in stored.items()}
        # A record ahead of the clock survived an archive rotation and can never
        # expire on its own; drop it here rather than let it hold a slot.
        pruned = {
            uri: record
            for uri, record in stored.items()
            if turns[uri] is not None and keep_from <= turns[uri] <= self._turn
        }
        if len(pruned) > MAX_LEDGER_URIS:
            ordered = sorted(pruned.items(), key=lambda item: turns[item[0]] or 0, reverse=True)
            pruned = dict(ordered[:MAX_LEDGER_URIS])

        payload = {
            "version": LEDGER_VERSION,
            "updated_turn": self._turn,
            "entries": pruned,
        }
        try:
            await viking_fs.write_file(
                uri=self._uri,
                content=json.dumps(payload, ensure_ascii=False),
                ctx=self._ctx,
            )
        except Exception as exc:
            logger.debug("Recall ledger write failed (%s); dedup stays best-effort", exc)
            self.status = "write_failed"
