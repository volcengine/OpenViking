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
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

LEDGER_FILENAME = ".recall_log.json"
LEDGER_VERSION = 1
MAX_LEDGER_URIS = 500
_WRITE_LOCK_TIMEOUT_SECONDS = 1.0


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _record_turn(record: Any) -> Optional[int]:
    return _as_int(record.get("turn", 0)) if isinstance(record, dict) else None


async def _resolve_clock(
    session: Any, session_uri: str, viking_fs: Any, ctx: RequestContext
) -> tuple[int, Optional[int]]:
    """Use cumulative messages when available, or the legacy archive and live count."""
    meta = getattr(session, "meta", None)
    total = _as_int(getattr(meta, "total_message_count", None))
    live = len(getattr(session, "messages", None) or [])
    if total is not None:
        return max(total, live), None

    # Session loads live messages and metadata separately. For legacy sessions,
    # read the count and generation together so a rotation cannot mix them.
    try:
        persisted = json.loads(await viking_fs.read_file(f"{session_uri}/.meta.json", ctx=ctx))
    except (NotFoundError, FileNotFoundError):
        return live, _as_int(getattr(meta, "commit_count", 0)) or 0
    count = _as_int(persisted.get("message_count"))
    return count if count is not None else live, _as_int(persisted.get("commit_count")) or 0


def _stored_entries(data: Dict[str, Any]) -> Dict[str, Any]:
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


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
        archive_generation: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
        status: str = "ok",
    ) -> None:
        self._service = service
        self._ctx = ctx
        self._uri = f"{session_uri.rstrip('/')}/{LEDGER_FILENAME}"
        self._turn = turn
        self._archive_generation = archive_generation
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
        session_uri = str(getattr(session, "uri", "") or "").rstrip("/")
        viking_fs = getattr(service, "viking_fs", None)
        if not session_uri or viking_fs is None:
            return None

        try:
            turn, archive_generation = await _resolve_clock(session, session_uri, viking_fs, ctx)
        except Exception as exc:
            logger.debug("Recall ledger clock unavailable (%s); skipping dedup", exc)
            return None
        ledger = cls(
            service=service,
            ctx=ctx,
            session_uri=session_uri,
            turn=turn,
            dedup_turns=dedup_turns,
            archive_generation=archive_generation,
        )
        try:
            ledger._data = await ledger._read(viking_fs)
            ledger.status = "ok"
        except Exception as exc:
            logger.debug("Recall ledger unreadable (%s); starting empty", exc)
            ledger._data = {}
            ledger.status = "new"
        return ledger

    @property
    def turn(self) -> int:
        return self._turn

    async def _read(self, viking_fs: Any) -> Dict[str, Any]:
        raw = await viking_fs.read_file(self._uri, ctx=self._ctx)
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}

    def cooled_uris(self) -> Set[str]:
        """URIs served within the last ``dedup_turns`` turns."""
        generation = _as_int(self._data.get("archive_generation"))
        if self._archive_generation is not None and generation not in (
            None,
            self._archive_generation,
        ):
            return set()
        cooled: Set[str] = set()
        for uri, record in _stored_entries(self._data).items():
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
        try:
            agfs = viking_fs._async_agfs
            path = viking_fs._uri_to_path(self._uri, ctx=self._ctx)
            fs_ctx = {"account_id": self._ctx.account_id}
            lease = await agfs.pathlock_acquire_exact(
                path, timeout_secs=_WRITE_LOCK_TIMEOUT_SECONDS, fs_ctx=fs_ctx
            )
            try:
                try:
                    latest = await self._read(viking_fs)
                except (NotFoundError, FileNotFoundError, json.JSONDecodeError):
                    latest = {}
                payload = self._merge(latest, entries)
                if payload is not None:
                    await viking_fs.write_file(
                        uri=self._uri,
                        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        ctx=self._ctx,
                        lease_ref=lease,
                    )
            finally:
                await agfs.pathlock_release(lease, fs_ctx=fs_ctx)
        except Exception as exc:
            logger.debug("Recall ledger write failed (%s); dedup stays best-effort", exc)
            self.status = "write_failed"

    def _merge(self, latest: Dict[str, Any], entries: Iterable[Any]) -> Optional[Dict[str, Any]]:
        generation = _as_int(latest.get("archive_generation"))
        if self._archive_generation is not None and generation is not None:
            if generation > self._archive_generation:
                return None
            if generation < self._archive_generation:
                latest = {}
        newest_turn = self._turn
        # An untagged legacy ledger can contain records from before a rotation.
        if self._archive_generation is None or generation is not None:
            newest_turn = max(newest_turn, _as_int(latest.get("updated_turn")) or 0)

        stored = dict(_stored_entries(latest))
        for entry in entries:
            uri = str(getattr(entry, "uri", "") or "")
            detail = getattr(entry, "detail", "")
            if not uri or detail == "uri":
                continue
            previous_turn = _record_turn(stored.get(uri))
            if previous_turn is not None and self._turn < previous_turn <= newest_turn:
                continue
            stored[uri] = {"turn": self._turn, "detail": detail}

        keep_from = self._turn - (self._dedup_turns * 4)
        turns = {uri: _record_turn(record) for uri, record in stored.items()}
        # A record ahead of the clock survived an archive rotation and can never
        # expire on its own; drop it here rather than let it hold a slot.
        pruned = {
            uri: record
            for uri, record in stored.items()
            if (served_turn := turns[uri]) is not None and keep_from <= served_turn <= newest_turn
        }
        if len(pruned) > MAX_LEDGER_URIS:
            ordered = sorted(pruned.items(), key=lambda item: turns[item[0]] or 0, reverse=True)
            pruned = dict(ordered[:MAX_LEDGER_URIS])

        payload: Dict[str, Any] = {
            "version": LEDGER_VERSION,
            "updated_turn": newest_turn,
            "entries": pruned,
        }
        if self._archive_generation is not None:
            payload["archive_generation"] = self._archive_generation
        return payload
