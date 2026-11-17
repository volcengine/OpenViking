# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite implementation of the product usage/audit store.

All time-keyed columns are persisted in UTC. Read methods accept a
viewer-supplied `tz` (an `Iana zoneinfo`-style `tzinfo`) and rebucket the
underlying hourly rows into user-local days / hours.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import RESET_ON_SCHEMA_UPGRADE_TABLES, SCHEMA_VERSION, SQLITE_SCHEMA
from .sql_store import SQLUsageAuditStore


class SQLiteUsageAuditStore(SQLUsageAuditStore):
    """Async wrapper around a local SQLite usage/audit database."""

    def __init__(self, db_path: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._db_path = Path(db_path)

    def _initialize_sync(self) -> None:
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self._db_path,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Preserve the compatible v4 layout through its additive migration.
        # Unknown newer transitions fail closed; older daily/local layouts
        # remain incompatible and use the reset path.
        self._migrate_legacy_sync(conn)
        conn.executescript(SQLITE_SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, ?)",
            ("version", str(SCHEMA_VERSION)),
        )
        self._conn = conn

    @staticmethod
    def _migrate_legacy_sync(conn: sqlite3.Connection) -> None:
        try:
            row = conn.execute("SELECT value FROM _schema_meta WHERE key = 'version'").fetchone()
        except sqlite3.OperationalError:
            row = None
        current = int(row["value"]) if row and row["value"] else 0
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                "Usage/audit database schema "
                f"version {current} is newer than supported version {SCHEMA_VERSION}"
            )
        if current == SCHEMA_VERSION:
            return
        if current == 4 and SCHEMA_VERSION == 5:
            SQLiteUsageAuditStore._migrate_v4_to_v5_sync(conn)
            return
        if current >= 4:
            raise RuntimeError(
                f"No usage/audit schema migration path from version {current} to {SCHEMA_VERSION}"
            )
        for table in RESET_ON_SCHEMA_UPGRADE_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    @staticmethod
    def _migrate_v4_to_v5_sync(conn: sqlite3.Connection) -> None:
        """Add nullable audit error fields without deleting v4 usage data."""
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'request_audit'"
        ).fetchone()
        if table is None:
            return
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(request_audit)")}
        conn.execute("BEGIN")
        try:
            for name in ("error_code", "error_message", "error_details"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE request_audit ADD COLUMN {name} TEXT")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
