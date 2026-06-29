# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable per-(harness, session) read-cursor store.

A single SQLite DB under ``~/.openviking/ingest/state.db`` records how far each
conversation has been ingested, so backfill and incremental polling resume after a
restart and never re-append already-ingested content.

(This is the read-position POINTER store; unrelated to the Cursor IDE harness.)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openviking.ingest.models import Cursor

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_cursor (
    harness            TEXT NOT NULL,
    native_session_id  TEXT NOT NULL,
    ov_session_id      TEXT,
    cursor_kind        TEXT NOT NULL,
    cursor_value       TEXT NOT NULL,
    locator            TEXT,
    title              TEXT,
    last_appended_count INTEGER NOT NULL DEFAULT 0,
    pending_tokens     INTEGER NOT NULL DEFAULT 0,
    last_committed_at  TEXT,
    updated_at         TEXT,
    PRIMARY KEY (harness, native_session_id)
);
"""


@dataclass
class CursorRecord:
    harness: str
    native_session_id: str
    ov_session_id: Optional[str]
    cursor: Cursor
    locator: Optional[str]
    title: Optional[str]
    last_appended_count: int
    pending_tokens: int
    last_committed_at: Optional[str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CursorStore:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir).expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "state.db"
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get(self, harness: str, native_session_id: str) -> Optional[CursorRecord]:
        row = self._conn.execute(
            "SELECT * FROM ingest_cursor WHERE harness = ? AND native_session_id = ?",
            (harness, native_session_id),
        ).fetchone()
        if row is None:
            return None
        return CursorRecord(
            harness=row["harness"],
            native_session_id=row["native_session_id"],
            ov_session_id=row["ov_session_id"],
            cursor=Cursor.from_json(row["cursor_kind"], row["cursor_value"]),
            locator=row["locator"],
            title=row["title"],
            last_appended_count=row["last_appended_count"],
            pending_tokens=row["pending_tokens"],
            last_committed_at=row["last_committed_at"],
        )

    def get_cursor(self, harness: str, native_session_id: str, kind: str) -> Optional[Cursor]:
        rec = self.get(harness, native_session_id)
        return rec.cursor if rec else None

    def upsert(
        self,
        harness: str,
        native_session_id: str,
        ov_session_id: str,
        cursor: Cursor,
        *,
        appended_delta: int = 0,
        pending_tokens: Optional[int] = None,
        committed: bool = False,
        locator: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        """Atomically advance a session's cursor and bookkeeping."""
        now = _now_iso()
        committed_at = now if committed else None
        self._conn.execute(
            """
            INSERT INTO ingest_cursor (
                harness, native_session_id, ov_session_id, cursor_kind, cursor_value,
                locator, title, last_appended_count, pending_tokens, last_committed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(harness, native_session_id) DO UPDATE SET
                ov_session_id = excluded.ov_session_id,
                cursor_kind   = excluded.cursor_kind,
                cursor_value  = excluded.cursor_value,
                locator       = COALESCE(excluded.locator, ingest_cursor.locator),
                title         = COALESCE(excluded.title, ingest_cursor.title),
                last_appended_count = ingest_cursor.last_appended_count + ?,
                pending_tokens = COALESCE(?, ingest_cursor.pending_tokens),
                last_committed_at = COALESCE(?, ingest_cursor.last_committed_at),
                updated_at = excluded.updated_at
            """,
            (
                harness,
                native_session_id,
                ov_session_id,
                cursor.kind,
                cursor.to_json(),
                locator,
                title,
                appended_delta,
                pending_tokens if pending_tokens is not None else 0,
                committed_at,
                now,
                # UPDATE-branch params:
                appended_delta,
                pending_tokens,
                committed_at,
            ),
        )
        self._conn.commit()

    def delete(self, harness: str, native_session_id: str) -> None:
        self._conn.execute(
            "DELETE FROM ingest_cursor WHERE harness = ? AND native_session_id = ?",
            (harness, native_session_id),
        )
        self._conn.commit()

    def all_records(self, harness: Optional[str] = None) -> list[CursorRecord]:
        if harness:
            rows = self._conn.execute(
                "SELECT harness, native_session_id FROM ingest_cursor WHERE harness = ?",
                (harness,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT harness, native_session_id FROM ingest_cursor"
            ).fetchall()
        out = []
        for r in rows:
            rec = self.get(r["harness"], r["native_session_id"])
            if rec:
                out.append(rec)
        return out
