# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite adapter with atomic local schema creation and WAL."""

import sqlite3
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from openviking.service.compile_store_sql import SQLCompileStore


class SQLiteCompileStore(SQLCompileStore):
    dialect = dependency = "sqlite"

    @staticmethod
    def _connection_options(options):
        unknown = options.keys() - {"path", "busy_timeout_ms", "maxsize"}
        if unknown:
            raise ValueError(
                f"Unknown SQLite Compile store parameters: {', '.join(sorted(unknown))}"
            )
        path = options.get("path")
        if not isinstance(path, (str, Path)) or not str(path).strip() or str(path) == ":memory:":
            raise ValueError("SQLite Compile store requires a persistent database path")
        try:
            timeout = int(str(options.get("busy_timeout_ms", 5000)))
            maxsize = int(str(options.get("maxsize", 10)))
        except ValueError as exc:
            raise ValueError("busy_timeout_ms and maxsize must be integers") from exc
        if timeout < 0 or maxsize < 1:
            raise ValueError("busy_timeout_ms must be non-negative and maxsize must be positive")
        return URL.create("sqlite+aiosqlite", database=str(Path(path).expanduser().resolve())), {
            "pool_size": maxsize,
            "max_overflow": 0,
            "connect_args": {"timeout": timeout / 1000},
        }

    @staticmethod
    def _is_duplicate(exc: IntegrityError) -> bool:
        return isinstance(exc.orig, sqlite3.IntegrityError) and str(exc.orig).startswith(
            "UNIQUE constraint failed: "
        )

    async def _prepare_schema(self, engine: AsyncEngine) -> None:
        assert self._url.database is not None
        Path(self._url.database).parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine.sync_engine, "connect")
        def configure_connection(connection, _record):
            connection.isolation_level = None
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

        async with engine.connect() as connection:
            mode = await connection.scalar(text("PRAGMA journal_mode=WAL"))
            if str(mode).lower() != "wal":
                raise RuntimeError("SQLite Compile store could not enable WAL")
            await connection.commit()
            async with connection.begin():
                await connection.execute(text("BEGIN IMMEDIATE"))
                await connection.run_sync(self._configs.metadata.create_all)
