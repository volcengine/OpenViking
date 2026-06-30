# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenCode (sst/opencode) adapter — SUPPORTED-EXPERIMENTAL.

Logs: ``~/.local/share/opencode/opencode.db`` (SQLite, WAL). ``session(id, title,
directory, model, time_created)``; ``message(id, session_id, time_created, data JSON
{role, modelID, providerID, …})``; the actual text lives in ``part(message_id,
time_created, data JSON{type, text})``. Polled read-only via (time_created, id) cursor.

Older OpenCode versions used a JSON file-store; that layout is deferred (clear error).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

from openviking.ingest.models import NormalizedMessage, SessionRef, iso_from_epoch_ms
from openviking.ingest.registry import register_source
from openviking.ingest.sources.base import NotSupportedError, SqliteLogSource


@register_source("opencode")
class OpenCodeSource(SqliteLogSource):
    def default_paths(self) -> List[Path]:
        return [Path.home() / ".local" / "share" / "opencode" / "opencode.db"]

    def db_path(self) -> Path:
        roots = self.roots()
        return roots[0] if roots else self.default_paths()[0]

    def discover_sessions(self):
        db = self.db_path()
        if not db.exists():
            legacy = Path.home() / ".local" / "share" / "opencode" / "project"
            if legacy.exists():
                raise NotSupportedError(
                    "opencode legacy file-store (~/.local/share/opencode/project) "
                    "is not supported (deferred); only opencode.db is supported"
                )
            return
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, title, directory, model, time_created "
                "FROM session ORDER BY time_created"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            yield SessionRef(
                harness=self.name,
                native_session_id=r["id"],
                locator=r["id"],
                title=r["title"],
                started_at=iso_from_epoch_ms(r["time_created"]),
                meta={"cwd": r["directory"], "session_model": r["model"]},
            )

    def fetch_rows(self, conn, ref: SessionRef, cursor, limit: int) -> List[sqlite3.Row]:
        t = cursor.value.get("time", 0)
        last_id = cursor.value.get("id", "")
        # opencode message ids are ULID-like (monotonic with insertion), so (time, id)
        # is a stable order/cursor.
        return conn.execute(
            "SELECT id, time_created, data FROM message "
            "WHERE session_id = ? AND (time_created > ? OR (time_created = ? AND id > ?)) "
            "ORDER BY time_created, id LIMIT ?",
            (ref.locator, t, t, last_id, limit),
        ).fetchall()

    def row_complete(self, conn, row) -> bool:
        # A message row may exist before its `part` text is flushed; only advance the
        # cursor past it once it is final, so late-arriving parts are never skipped.
        try:
            data = json.loads(row["data"])
        except (ValueError, TypeError):
            return True
        if data.get("role") == "user":
            return True
        return bool((data.get("time") or {}).get("completed") or data.get("finish"))

    def rows_to_messages(self, conn, ref: SessionRef, rows) -> List[NormalizedMessage]:
        out: List[NormalizedMessage] = []
        for row in rows:
            try:
                data = json.loads(row["data"])
            except (ValueError, TypeError):
                continue
            role = data.get("role")
            if role not in ("user", "assistant"):
                continue

            text = self._reassemble_text(conn, row["id"])
            if not text:
                continue

            if role == "assistant":
                model = data.get("modelID") or ref.meta.get("session_model")
                peer = self.assistant_peer(model, data.get("providerID"))
            else:
                model = data.get("modelID")
                peer = self.user_peer(cwd=ref.meta.get("cwd"))
            out.append(
                NormalizedMessage(
                    role=role,
                    text=text,
                    created_at=iso_from_epoch_ms(row["time_created"]),
                    peer_id=peer,
                    meta={
                        "model": data.get("modelID"),
                        "provider": data.get("providerID"),
                        "cwd": ref.meta.get("cwd"),
                    },
                )
            )
        return out

    @staticmethod
    def _reassemble_text(conn, message_id: str) -> str:
        parts = conn.execute(
            "SELECT data FROM part WHERE message_id = ? ORDER BY time_created, id",
            (message_id,),
        ).fetchall()
        chunks: List[str] = []
        for p in parts:
            try:
                pd = json.loads(p["data"])
            except (ValueError, TypeError):
                continue
            if pd.get("type") == "text":
                text = pd.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
        return "\n".join(chunks).strip()
