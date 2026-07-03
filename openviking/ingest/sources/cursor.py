# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cursor (IDE) adapter — DEFERRED stub.

NOTE: this is the Cursor *IDE harness*, distinct from the read-position ``Cursor``
pointer in ``models.py``.

Cursor stores chat in ``~/Library/Application Support/Cursor/User/{globalStorage,
workspaceStorage/*}/state.vscdb`` (SQLite key-value: ``ItemTable`` / ``cursorDiskKV``),
with a single conversation spread across ``composerData:*`` / ``bubbleId:*`` /
``agentKv:*`` BLOB→JSON keys. The schema is undocumented, drifts between Cursor
versions, and offers no monotonic cursor for clean incremental reads — so ingestion is
deferred. The adapter is registered (so it shows up in ``list-sources``) but refuses to
run unless explicitly opted in (``enabled`` + ``experimental``), and even then is not yet
implemented.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from openviking.ingest.registry import register_source
from openviking.ingest.sources.base import NotSupportedError, SqliteLogSource


@register_source("cursor")
class CursorIDESource(SqliteLogSource):
    def default_paths(self) -> List[Path]:
        base = Path.home() / "Library" / "Application Support" / "Cursor" / "User"
        return [base / "globalStorage" / "state.vscdb"]

    def db_path(self) -> Path:
        roots = self.roots()
        return roots[0] if roots else self.default_paths()[0]

    def _guard(self) -> None:
        if not (self.cfg.enabled and self.cfg.experimental):
            raise NotSupportedError(
                "cursor ingest is deferred/experimental; set both enabled=true and "
                "experimental=true to opt in"
            )
        raise NotSupportedError(
            "cursor ingest is not implemented: state.vscdb stores conversations as "
            "undocumented, version-unstable KV blobs (composerData/bubbleId/agentKv) "
            "with no monotonic cursor"
        )

    def discover_sessions(self):
        self._guard()
        return []

    def fetch_rows(self, conn, ref, cursor, limit):  # pragma: no cover - deferred
        self._guard()
        return []

    def rows_to_messages(self, conn, ref, rows):  # pragma: no cover - deferred
        self._guard()
        return []
