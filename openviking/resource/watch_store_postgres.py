# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL Watch metadata, occupancy and encrypted source credentials."""

import logging
from typing import Any, Sequence

from sqlalchemy import JSON, func, literal_column, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError

from openviking.resource.watch_store_sql import SQLWatchStore

logger = logging.getLogger(__name__)

_SCHEMA_SCRIPT = "openviking/service/sql/001_watch_tasks_postgres.sql"


class PostgresWatchStore(SQLWatchStore):
    dialect_name = "postgresql"
    target_prefix_index_chars = 128

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        return getattr(exc.orig, "sqlstate", None) == "23505"

    @property
    def provider(self) -> str:
        return "postgres"

    @property
    def _target_key_expression(self) -> Any:
        # Keep index constants literal so prepared generic plans can match the DDL.
        return func.substr(self._tasks.c.target_uri_key, literal_column("1"), literal_column("128"))

    async def _validate_schema(self, session: Any) -> None:
        try:
            await super()._validate_schema(session)
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if state not in {"42P01", "42703"}:
                raise
            problem = "does not exist" if state == "42P01" else "is missing required columns"
            raise RuntimeError(
                f"PostgreSQL Watch table {self._tasks.name!r} {problem}. "
                f"Please have an administrator provision or migrate it using {_SCHEMA_SCRIPT}; "
                "replace table and index names for storage.watch_store.params.table. "
                "Initialization does not create or migrate the schema."
            ) from exc

        # Resolve the same quoted relation as the column probe, including search_path.
        result = await session.execute(
            text(
                """
                SELECT i.indisvalid AND i.indisready AND i.indpred IS NULL
                       AND am.amname = 'btree' AS usable,
                       ARRAY(
                           SELECT pg_catalog.pg_get_indexdef(i.indexrelid, k, true)
                           FROM generate_series(1, i.indnkeyatts) AS k
                       ) AS keys
                FROM pg_catalog.pg_index AS i
                JOIN pg_catalog.pg_class AS x ON x.oid = i.indexrelid
                JOIN pg_catalog.pg_am AS am ON am.oid = x.relam
                WHERE i.indrelid = pg_catalog.to_regclass(:table)
                """
            ),
            {"table": '"' + self._tasks.name.replace('"', '""') + '"'},
        )
        usable = {tuple(row["keys"]) for row in result.mappings() if row["usable"]}
        # pg_get_indexdef includes PostgreSQL's implicit varchar-to-text casts.
        # Compare definitions rather than names so equivalent renamed indexes work.
        expected = {
            "due": ("service_name", "enabled", "next_run_at"),
            "run": ("service_name", "run_started_at"),
            "target": ("service_name", "account_id", "target_uri_hash"),
            "target_prefix": (
                "service_name",
                "account_id",
                "deleted",
                "substr(target_uri_key::text, 1, 128)",
            ),
            "list_all": ("service_name", "deleted", "created_at", "task_id"),
            "list": (
                "service_name",
                "md5(account_id::text)",
                "md5(user_id::text)",
                "deleted",
                "created_at",
                "task_id",
            ),
        }
        missing = [
            f"ix_{self._tasks.name}_{suffix}"
            for suffix, keys in expected.items()
            if keys not in usable
        ]
        if missing:
            logger.warning(
                "PostgreSQL Watch table %r is missing usable indexes "
                "(absent, invalid, or incompatible): %s. Queries may be slower. "
                "Have an administrator apply the index definitions from %s to this existing "
                "table, replacing table/index names and repairing or replacing incompatible "
                "or invalid indexes first. Initialization does not create or migrate indexes.",
                self._tasks.name,
                ", ".join(missing),
                _SCHEMA_SCRIPT,
            )

    @staticmethod
    def _connection_options(values: dict[str, Any]) -> tuple[URL, dict[str, Any]]:
        allowed = {
            "user",
            "password",
            "database",
            "host",
            "port",
            "maxsize",
            "connect_timeout",
            "sslmode",
            "sslrootcert",
            "sslcert",
            "sslkey",
        }
        unknown = values.keys() - allowed
        if unknown:
            raise ValueError(
                f"Unknown PostgreSQL Watch store parameters: {', '.join(sorted(unknown))}"
            )
        missing = [name for name in ("user", "database") if not values.get(name)]
        if missing:
            raise ValueError(f"PostgreSQL Watch store requires: {', '.join(missing)}")
        try:
            maxsize = int(values.get("maxsize", 10))
            port = int(values.get("port", 5432))
            timeout = int(values.get("connect_timeout", 10))
        except (TypeError, ValueError) as exc:
            raise ValueError("maxsize, port and connect_timeout must be integers") from exc
        if maxsize < 1 or not 1 <= port <= 65535 or timeout < 1:
            raise ValueError(
                "maxsize and connect_timeout must be positive; port must be between 1 and 65535"
            )
        sslmode = values.get("sslmode", "prefer")
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise ValueError("Invalid PostgreSQL sslmode")
        connect_args = {"connect_timeout": timeout, "sslmode": sslmode}
        for name in ("sslrootcert", "sslcert", "sslkey"):
            if name in values:
                connect_args[name] = str(values[name])
        return URL.create(
            "postgresql+psycopg",
            username=str(values["user"]),
            password=str(values["password"]) if values.get("password") is not None else None,
            host=str(values.get("host", "127.0.0.1")),
            port=port,
            database=str(values["database"]),
        ), {
            "pool_size": maxsize,
            "max_overflow": 0,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
            "connect_args": connect_args,
            "isolation_level": "READ COMMITTED",
        }

    def _public_payload(self, names: Sequence[str]) -> Any:
        args: list[Any] = []
        for name in names:
            args.extend([name, self._tasks.c.payload[name]])
        return func.json_build_object(*args, type_=JSON)
