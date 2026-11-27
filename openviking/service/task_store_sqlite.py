# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite connection and write transactions for the durable Task/Work store."""

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from openviking.service.task_store_sql import SQLTaskWorkStore


class SQLiteTaskWorkStore(SQLTaskWorkStore):
    """Local durable Task/Work storage with WAL and serialized write transactions."""

    provider_name = "SQLite"
    dependency_extra = "sqlite"
    dialect_name = "sqlite"
    SCHEMA_VERSION = 1

    @staticmethod
    def _connection_options(values: dict[str, object]) -> tuple[URL, dict[str, object]]:
        unknown = values.keys() - {"path", "busy_timeout_ms", "maxsize"}
        if unknown:
            raise ValueError(f"Unknown SQLite task store parameters: {', '.join(sorted(unknown))}")
        path = values.get("path")
        if not isinstance(path, (str, Path)) or not str(path).strip() or str(path) == ":memory:":
            raise ValueError("SQLite task store requires a persistent database path")
        try:
            timeout = int(str(values.get("busy_timeout_ms", 5000)))
            maxsize = int(str(values.get("maxsize", 10)))
        except (TypeError, ValueError) as exc:
            raise ValueError("busy_timeout_ms and maxsize must be integers") from exc
        if timeout < 0 or maxsize < 1:
            raise ValueError("busy_timeout_ms must be non-negative and maxsize must be positive")
        return URL.create("sqlite+aiosqlite", database=str(Path(path).expanduser().resolve())), {
            "pool_size": maxsize,
            "max_overflow": 0,
            "connect_args": {"timeout": timeout / 1000},
        }

    def _configure_engine(self, engine: AsyncEngine) -> None:
        assert self._url.database is not None
        Path(self._url.database).parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine.sync_engine, "connect")
        def configure_connection(connection: Any, _record: Any) -> None:
            # The write context issues BEGIN explicitly; ordinary reads stay read-only.
            connection.isolation_level = None

    async def _initialize_schema(self, engine: AsyncEngine) -> None:
        async with engine.connect() as connection:
            mode = await connection.scalar(text("PRAGMA journal_mode=WAL"))
            if str(mode).lower() != "wal":
                raise RuntimeError("SQLite task store could not enable WAL")
            await connection.commit()
            async with connection.begin():
                await connection.execute(text("BEGIN IMMEDIATE"))
                version = await connection.scalar(text("PRAGMA user_version"))
                if version not in (0, self.SCHEMA_VERSION):
                    raise RuntimeError(f"Unsupported SQLite task schema version: {version}")
                await connection.run_sync(self._models.Base.metadata.create_all)
                if version == 0:
                    await connection.execute(text(f"PRAGMA user_version={self.SCHEMA_VERSION}"))

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(text("BEGIN IMMEDIATE"))
                yield session

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        return isinstance(exc.orig, sqlite3.IntegrityError) and str(exc.orig).startswith(
            "UNIQUE constraint failed: "
        )

    def _is_business_key_conflict_error(self, exc: IntegrityError) -> bool:
        table = self._models.table_prefix
        return self._is_duplicate_error(exc) and str(exc.orig) == (
            f"UNIQUE constraint failed: {table}.service_name, {table}.active_business_key"
        )
