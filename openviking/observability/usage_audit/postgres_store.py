# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Optional shared PostgreSQL backend for the existing Console projections."""

from __future__ import annotations

import re

from .schema import SCHEMA_VERSION, SQLITE_SCHEMA
from .sql_store import SQLUsageAuditStore


class _PostgresConnection:
    """Bind the shared, parameterized qmark SQL to psycopg's DB-API dialect.

    Only owner-defined SQL goes through this adapter; event strings are always
    bound parameters. The shared queries contain no literal question marks.
    """

    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, parameters=None):
        return self.connection.execute(query.replace("?", "%s"), parameters)

    def executemany(self, query, parameters):
        with self.connection.cursor() as cursor:
            cursor.executemany(query.replace("?", "%s"), parameters)

    def close(self):
        self.connection.close()


class PostgresUsageAuditStore(SQLUsageAuditStore):
    """One bounded connection per runtime, shared tables across server instances.

    A failed/ambiguous commit is surfaced to the existing worker, never retried
    here: additive projections cannot safely replay an uncertain batch.
    """

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "openviking_usage_audit",
        connect_timeout_seconds: int = 10,
        statement_timeout_seconds: int = 30,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise ValueError("Usage/audit PostgreSQL schema must be a lowercase SQL identifier")
        if schema.startswith("pg_") or schema in {"public", "information_schema"}:
            raise ValueError("Usage/audit PostgreSQL requires a dedicated schema")
        if connect_timeout_seconds <= 0 or statement_timeout_seconds <= 0:
            raise ValueError("PostgreSQL timeouts must be positive")
        self._dsn = dsn
        self._schema = schema
        self._connect_timeout = connect_timeout_seconds
        self._statement_timeout = statement_timeout_seconds * 1000

    def _initialize_sync(self) -> None:
        if self._conn is not None:
            return
        try:
            import psycopg
            from psycopg import sql
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ImportError("PostgreSQL usage/audit requires openviking[postgres]") from exc

        conn = psycopg.connect(
            self._dsn,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=self._connect_timeout,
            # Apply before DDL/locks, even if the DSN contains its own options.
            options=f"-c statement_timeout={self._statement_timeout} -c timezone=UTC",
        )
        try:
            with conn.transaction():
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"ov-usage-schema:{self._schema}",),
                )
                conn.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self._schema))
                )
                # A dedicated schema prevents resolving missing tables through public.
                conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self._schema)))
                tables = conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = %s",
                    (self._schema,),
                ).fetchall()
                if tables and "_schema_meta" not in {row["tablename"] for row in tables}:
                    raise RuntimeError(
                        "Unversioned PostgreSQL usage/audit schema; migration required"
                    )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                row = conn.execute(
                    "SELECT value FROM _schema_meta WHERE key = 'version'"
                ).fetchone()
                if row is None and tables:
                    raise RuntimeError(
                        "Unversioned PostgreSQL usage/audit schema; migration required"
                    )
                if row is not None and int(row["value"]) != SCHEMA_VERSION:
                    raise RuntimeError(
                        "Unsupported PostgreSQL usage/audit schema version; migration required"
                    )
                # Same shape and semantics as SQLite; no destructive reset or
                # automatic import from another backend on a configuration switch.
                ddl = SQLITE_SCHEMA.replace(
                    "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
                )
                ddl = ddl.replace("INTEGER NOT NULL DEFAULT 0", "BIGINT NOT NULL DEFAULT 0")
                ddl = ddl.replace("duration_ms REAL", "duration_ms DOUBLE PRECISION")
                for statement in ddl.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                from .performance_store import initialize

                initialize(conn, postgres=True)
                conn.execute(
                    "INSERT INTO _schema_meta (key, value) VALUES ('version', %s) "
                    "ON CONFLICT (key) DO NOTHING",
                    (str(SCHEMA_VERSION),),
                )
            self._conn = _PostgresConnection(conn)
        except BaseException:
            conn.close()
            raise

    def _begin_write(self, accounts: set[str]) -> None:
        super()._begin_write(accounts)
        try:
            # Initialization owns this same key exclusively before touching DDL.
            # Acquire the shared schema lease before any projection table lock,
            # then the account locks. This prevents startup/write lock inversion.
            self._conn.execute(
                "SELECT pg_advisory_xact_lock_shared(hashtextextended(?, 0))",
                (f"ov-usage-schema:{self._schema}",),
            )
            # Serialize only an account's flush/purge transactions. This keeps
            # the per-account retention cap correct under concurrent writers.
            for account in sorted(accounts):
                self._conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    (f"ov-usage-account:{self._schema}:{account}",),
                )
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
