# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable per-(harness, session) read-cursor + commit/idempotency state.

A single SQLite DB under ``~/.openviking/ingest/state.db`` records how far each
conversation has been ingested, plus:
- ``needs_commit``: appended-but-not-yet-committed, so a crash/error between append and
  commit still gets its memory extraction on the next pass (not stranded forever);
- a ``pending`` batch intent (cursor + count + server-message baseline) written BEFORE a
  batch is appended, so a crash mid-append is reconciled against the server's message
  count on restart instead of blindly re-appending (duplicate-safe).

A ``SingleInstanceLock`` prevents two ingest processes from racing on the same state.

(This is the read-position POINTER store; unrelated to the Cursor IDE harness.)
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openviking.ingest.models import Cursor
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

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
    needs_commit       INTEGER NOT NULL DEFAULT 0,
    pend_cursor        TEXT,
    pend_count         INTEGER NOT NULL DEFAULT 0,
    pend_baseline      INTEGER NOT NULL DEFAULT 0,
    last_committed_at  TEXT,
    updated_at         TEXT,
    PRIMARY KEY (harness, native_session_id)
);
"""

_MIGRATION_COLUMNS = {
    "needs_commit": "INTEGER NOT NULL DEFAULT 0",
    "pend_cursor": "TEXT",
    "pend_count": "INTEGER NOT NULL DEFAULT 0",
    "pend_baseline": "INTEGER NOT NULL DEFAULT 0",
}


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
    needs_commit: bool
    pending_cursor: Optional[Cursor]
    pending_count: int
    pending_baseline: int
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
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        existing = {r["name"] for r in self._conn.execute("PRAGMA table_info(ingest_cursor)")}
        for col, decl in _MIGRATION_COLUMNS.items():
            if col not in existing:
                self._conn.execute(f"ALTER TABLE ingest_cursor ADD COLUMN {col} {decl}")

    def close(self) -> None:
        self._conn.close()

    # --- reads ------------------------------------------------------------
    def get(self, harness: str, native_session_id: str) -> Optional[CursorRecord]:
        row = self._conn.execute(
            "SELECT * FROM ingest_cursor WHERE harness = ? AND native_session_id = ?",
            (harness, native_session_id),
        ).fetchone()
        if row is None:
            return None
        pend = (
            Cursor.from_json(row["cursor_kind"], row["pend_cursor"]) if row["pend_cursor"] else None
        )
        return CursorRecord(
            harness=row["harness"],
            native_session_id=row["native_session_id"],
            ov_session_id=row["ov_session_id"],
            cursor=Cursor.from_json(row["cursor_kind"], row["cursor_value"]),
            locator=row["locator"],
            title=row["title"],
            last_appended_count=row["last_appended_count"],
            pending_tokens=row["pending_tokens"],
            needs_commit=bool(row["needs_commit"]),
            pending_cursor=pend,
            pending_count=row["pend_count"],
            pending_baseline=row["pend_baseline"],
            last_committed_at=row["last_committed_at"],
        )

    def get_cursor(self, harness: str, native_session_id: str, kind: str) -> Optional[Cursor]:
        rec = self.get(harness, native_session_id)
        return rec.cursor if rec else None

    # --- writes -----------------------------------------------------------
    def ensure_row(
        self,
        harness: str,
        native_session_id: str,
        ov_session_id: str,
        cursor: Cursor,
        *,
        locator: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        """Insert a zero/initial row if absent (idempotent)."""
        self._conn.execute(
            """INSERT OR IGNORE INTO ingest_cursor
               (harness, native_session_id, ov_session_id, cursor_kind, cursor_value,
                locator, title, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                harness,
                native_session_id,
                ov_session_id,
                cursor.kind,
                cursor.to_json(),
                locator,
                title,
                _now_iso(),
            ),
        )
        self._conn.commit()

    def advance_cursor(
        self,
        harness: str,
        native_session_id: str,
        ov_session_id: str,
        cursor: Cursor,
        *,
        locator: Optional[str] = None,
    ) -> None:
        """Advance the confirmed cursor only (e.g. a read that yielded no messages)."""
        self.ensure_row(harness, native_session_id, ov_session_id, cursor, locator=locator)
        self._conn.execute(
            "UPDATE ingest_cursor SET cursor_value = ?, cursor_kind = ?, "
            "locator = COALESCE(?, locator), updated_at = ? "
            "WHERE harness = ? AND native_session_id = ?",
            (cursor.to_json(), cursor.kind, locator, _now_iso(), harness, native_session_id),
        )
        self._conn.commit()

    def set_pending(
        self,
        harness: str,
        native_session_id: str,
        ov_session_id: str,
        from_cursor: Cursor,
        pend_cursor: Cursor,
        pend_count: int,
        baseline: int,
        *,
        locator: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        """Durably record a batch intent BEFORE appending it (crash reconciliation)."""
        self.ensure_row(
            harness, native_session_id, ov_session_id, from_cursor, locator=locator, title=title
        )
        self._conn.execute(
            "UPDATE ingest_cursor SET ov_session_id = ?, pend_cursor = ?, pend_count = ?, "
            "pend_baseline = ?, locator = COALESCE(?, locator), title = COALESCE(?, title), "
            "updated_at = ? WHERE harness = ? AND native_session_id = ?",
            (
                ov_session_id,
                pend_cursor.to_json(),
                pend_count,
                baseline,
                locator,
                title,
                _now_iso(),
                harness,
                native_session_id,
            ),
        )
        self._conn.commit()

    def confirm_append(
        self, harness: str, native_session_id: str, cursor: Cursor, appended_delta: int
    ) -> None:
        """Confirm a landed batch: advance cursor, accrue count, mark needs_commit, clear pending."""
        self._conn.execute(
            "UPDATE ingest_cursor SET cursor_value = ?, cursor_kind = ?, "
            "last_appended_count = last_appended_count + ?, needs_commit = 1, "
            "pend_cursor = NULL, pend_count = 0, pend_baseline = 0, updated_at = ? "
            "WHERE harness = ? AND native_session_id = ?",
            (cursor.to_json(), cursor.kind, appended_delta, _now_iso(), harness, native_session_id),
        )
        self._conn.commit()

    def clear_pending(self, harness: str, native_session_id: str) -> None:
        self._conn.execute(
            "UPDATE ingest_cursor SET pend_cursor = NULL, pend_count = 0, pend_baseline = 0, "
            "updated_at = ? WHERE harness = ? AND native_session_id = ?",
            (_now_iso(), harness, native_session_id),
        )
        self._conn.commit()

    def mark_committed(
        self, harness: str, native_session_id: str, *, pending_tokens: int = 0
    ) -> None:
        self._conn.execute(
            "UPDATE ingest_cursor SET needs_commit = 0, pending_tokens = ?, "
            "last_committed_at = ?, updated_at = ? WHERE harness = ? AND native_session_id = ?",
            (pending_tokens, _now_iso(), _now_iso(), harness, native_session_id),
        )
        self._conn.commit()

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
        """General cursor upsert (used by tests and simple cursor advances)."""
        now = _now_iso()
        committed_at = now if committed else None
        self._conn.execute(
            """INSERT INTO ingest_cursor
               (harness, native_session_id, ov_session_id, cursor_kind, cursor_value,
                locator, title, last_appended_count, pending_tokens, last_committed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(harness, native_session_id) DO UPDATE SET
                 ov_session_id = excluded.ov_session_id,
                 cursor_kind = excluded.cursor_kind, cursor_value = excluded.cursor_value,
                 locator = COALESCE(excluded.locator, ingest_cursor.locator),
                 title = COALESCE(excluded.title, ingest_cursor.title),
                 last_appended_count = ingest_cursor.last_appended_count + ?,
                 pending_tokens = COALESCE(?, ingest_cursor.pending_tokens),
                 last_committed_at = COALESCE(?, ingest_cursor.last_committed_at),
                 updated_at = excluded.updated_at""",
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


class SingleInstanceLock:
    """Best-effort exclusive lock so two ingest processes don't race on one state dir."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir).expanduser() / "ingest.lock"
        self._fh = None

    def acquire(self) -> "SingleInstanceLock":
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX
            logger.warning("[ingest] fcntl unavailable; single-instance lock disabled")
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fh.close()
            self._fh = None
            raise RuntimeError(f"another ingest process already holds {self.path}") from exc
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            import fcntl

            fcntl.flock(self._fh, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
