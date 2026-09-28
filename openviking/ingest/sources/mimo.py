# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Xiaomi MiMo Desktop / MiMoCode adapter — EXPERIMENTAL.

Logs: ``~/.local/share/mimocode/mimocode.db`` (SQLite, WAL). Schema::

    session(id, title, directory, version, time_created, …)
    message(id, session_id, agent_id, time_created, data JSON{role, modelID,
            providerID, time{created,completed}, finish, system, …})
    part(id, message_id, session_id, time_created, data JSON{type, text,
         synthetic, …})

``message.time_created`` is epoch **milliseconds**. Conversation text lives in
``part`` rows with ``type == "text"``. Tool / reasoning / step parts are skipped.

Host pollution that must not become memory:

* ``message.data.system`` holds the full system prompt on every user turn.
* ``part.synthetic == true`` marks injected context (runtime notes, tool lists,
  skill bodies) written as ``type=text``.
* residual ``<system-reminder>…</system-reminder>`` may remain in a
  non-synthetic text block.

Cursor is ``(time_created, id)`` over ``message`` (same model as ``opencode``).
Only ``agent_id = 'main'`` rows are read. An assistant row is complete only when
``time.completed`` or ``finish`` is set, so late-arriving parts are not skipped.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from openviking.ingest.models import NormalizedMessage, SessionRef, iso_from_epoch_ms
from openviking.ingest.registry import register_source
from openviking.ingest.sources.base import NotSupportedError, SqliteLogSource

_REMINDER_RE = re.compile(r"<system-reminder\b[^>]*>.*?</system-reminder>", re.S | re.I)


def _clean_text(raw: str) -> str:
    return _REMINDER_RE.sub("", raw).strip()


@register_source("mimo")
class MiMoSource(SqliteLogSource):
    def default_paths(self) -> List[Path]:
        return [Path.home() / ".local" / "share" / "mimocode" / "mimocode.db"]

    def db_path(self) -> Path:
        roots = self.roots()
        if not roots:
            return self.default_paths()[0]
        path = roots[0].expanduser()
        return path / "mimocode.db" if path.is_dir() else path

    def discover_sessions(self):
        db = self.db_path()
        if not db.exists():
            raise NotSupportedError(f"mimo: database not found at {db}")
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, title, directory, version, time_created "
                "FROM session ORDER BY time_created"
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            yield SessionRef(
                harness=self.name,
                native_session_id=row["id"],
                locator=row["id"],
                title=row["title"],
                started_at=iso_from_epoch_ms(row["time_created"]),
                meta={
                    "cwd": row["directory"],
                    "version": row["version"],
                    "platform": "mimo",
                },
            )

    def fetch_rows(self, conn, ref: SessionRef, cursor, limit: int) -> List[sqlite3.Row]:
        time_at = cursor.value.get("time", 0)
        last_id = cursor.value.get("id", "")
        return conn.execute(
            "SELECT id, agent_id, time_created, data FROM message "
            "WHERE session_id = ? AND agent_id = 'main' "
            "AND (time_created > ? OR (time_created = ? AND id > ?)) "
            "ORDER BY time_created, id LIMIT ?",
            (ref.locator, time_at, time_at, last_id, limit),
        ).fetchall()

    def row_complete(self, conn, row) -> bool:
        try:
            data = json.loads(row["data"])
        except (ValueError, TypeError):
            return True
        if data.get("role") == "user":
            return True
        time_obj = data.get("time") or {}
        if isinstance(time_obj, dict) and time_obj.get("completed"):
            return True
        return bool(data.get("finish"))

    def rows_to_messages(self, conn, ref: SessionRef, rows) -> List[NormalizedMessage]:
        out: List[NormalizedMessage] = []
        cwd = ref.meta.get("cwd")
        for row in rows:
            try:
                data = json.loads(row["data"])
            except (ValueError, TypeError):
                continue
            role = data.get("role")
            if role not in ("user", "assistant"):
                continue

            text = self._reassemble_text(conn, row["id"], user=(role == "user"))
            if not text:
                continue

            if role == "assistant":
                peer = self.assistant_peer(data.get("modelID"), data.get("providerID"))
            else:
                peer = self.user_peer(cwd=cwd)

            out.append(
                NormalizedMessage(
                    role=role,
                    text=text,
                    created_at=iso_from_epoch_ms(row["time_created"]),
                    peer_id=peer,
                    meta={
                        "model": data.get("modelID"),
                        "provider": data.get("providerID"),
                        "cwd": cwd,
                    },
                )
            )
        return out

    @staticmethod
    def _reassemble_text(conn, message_id: str, *, user: bool) -> str:
        parts = conn.execute(
            "SELECT data FROM part WHERE message_id = ? ORDER BY time_created, id",
            (message_id,),
        ).fetchall()
        chunks: List[str] = []
        for part in parts:
            try:
                payload: Dict[str, Any] = json.loads(part["data"])
            except (ValueError, TypeError):
                continue
            if payload.get("type") != "text" or payload.get("synthetic"):
                continue
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            cleaned = _clean_text(text) if user else text.strip()
            if cleaned:
                chunks.append(cleaned)
        return "\n".join(chunks).strip()
