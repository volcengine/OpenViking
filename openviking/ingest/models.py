# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Core data structures shared across the ingest subsystem.

Note on "cursor": throughout this package, ``Cursor`` / ``cursor_store`` / ``cursor_kind``
refer to the read-position POINTER (how far we have ingested a given log), NOT the Cursor
IDE harness (whose adapter is ``sources/cursor.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def iso_from_epoch_ms(value: Any) -> Optional[str]:
    """Best-effort ISO-8601 from an epoch-millisecond integer (used by SQLite/JSONL adapters)."""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    # Heuristic: 10-digit values are seconds, 13-digit are milliseconds.
    seconds = ms / 1000.0 if ms > 10_000_000_000 else float(ms)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# Cursor kinds.
BYTE_OFFSET = "byte_offset"  # append-only JSONL: byte offset of last consumed line
ROWID_TIME = "rowid_time"  # SQLite: (time_created, id) of last consumed row


@dataclass
class NormalizedMessage:
    """A single conversation turn, harness-agnostic, ready for OV replay.

    ``peer_id`` is resolved by the adapter (see ``peer.py``): assistant turns ->
    ``{harness}/{model}``; user turns -> a human identifier (git identity for
    single-user dev harnesses, original username for group-chat harnesses).
    """

    role: str  # "user" | "assistant"
    text: str = ""
    parts: List[Dict[str, Any]] = field(default_factory=list)  # extra tool/context parts
    created_at: Optional[str] = None  # ISO-8601
    peer_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)  # model, provider, cwd, …


@dataclass
class SessionRef:
    """A discoverable conversation in a harness's storage."""

    harness: str  # registry name, e.g. "claude_code"
    native_session_id: str  # the harness's own session/conversation id
    locator: str  # file path (JSONL) or db session id (SQLite) used to read
    title: Optional[str] = None
    started_at: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)  # session-level model, cwd, platform


@dataclass
class Cursor:
    """A durable read-position pointer for one (harness, session)."""

    kind: str  # BYTE_OFFSET | ROWID_TIME
    value: Dict[str, Any]  # {"offset": int} | {"time": int|float, "id": str}

    @classmethod
    def zero(cls, kind: str) -> "Cursor":
        if kind == BYTE_OFFSET:
            return cls(kind=kind, value={"offset": 0})
        return cls(kind=kind, value={"time": 0, "id": ""})

    @property
    def offset(self) -> int:
        return int(self.value.get("offset", 0))

    def to_json(self) -> str:
        return json.dumps(self.value, separators=(",", ":"))

    @classmethod
    def from_json(cls, kind: str, raw: Optional[str]) -> "Cursor":
        if not raw:
            return cls.zero(kind)
        try:
            return cls(kind=kind, value=json.loads(raw))
        except (ValueError, TypeError):
            return cls.zero(kind)
