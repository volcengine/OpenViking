# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite Watch persistence for processes sharing a database on one host."""

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles

from openviking.resource.watch_store_sql import SQLWatchStore, db_now


@compiles(db_now, "sqlite")
def _sqlite_now(element: Any, compiler: Any, **kw: Any) -> str:
    # A per-connection function avoids the millisecond resolution of strftime
    # and keeps reservation timestamps distinct when a retry starts immediately.
    return "ov_watch_now()"


class SQLiteWatchStore(SQLWatchStore):
    dialect_name = "sqlite"

    @staticmethod
    def _connection_options(values: dict[str, Any]) -> tuple[URL, dict[str, Any]]:
        unknown = values.keys() - {"path", "busy_timeout_ms"}
        if unknown:
            raise ValueError(f"Unknown SQLite watch store parameters: {', '.join(sorted(unknown))}")
        path = values.get("path")
        if not isinstance(path, str) or not path or not Path(path).is_absolute():
            raise ValueError("SQLite watch store requires an absolute local database path")
        timeout = values.get("busy_timeout_ms", 5000)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("SQLite watch store busy_timeout_ms must be a positive integer")
        return URL.create("sqlite+aiosqlite", database=str(Path(path).resolve())), {
            "connect_args": {"timeout": timeout / 1000},
            "pool_size": 1,
            "max_overflow": 0,
        }

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        # Extended codes for SQLITE_CONSTRAINT_PRIMARYKEY and UNIQUE.
        return getattr(exc.orig, "sqlite_errorcode", None) in {1555, 2067} or (
            # Python 3.10 does not expose sqlite_errorcode.
            str(exc.orig).startswith("UNIQUE constraint failed:")
        )

    async def _prepare_engine(self, engine: Any) -> None:
        import asyncio
        import time

        await asyncio.to_thread(Path(self._url.database).parent.mkdir, parents=True, exist_ok=True)

        @event.listens_for(engine.sync_engine, "connect")
        def connect(connection: Any, record: Any) -> None:
            connection.isolation_level = None
            connection.create_function("ov_watch_now", 0, time.time)

        @event.listens_for(engine.sync_engine, "begin")
        def begin(connection: Any) -> None:
            # SQLite ignores SELECT FOR UPDATE. Acquire the writer lock before
            # reading so read/modify/write operations cannot lose an update.
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        async with engine.begin() as connection:
            await connection.run_sync(self._tasks.metadata.create_all)
