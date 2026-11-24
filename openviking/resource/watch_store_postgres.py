# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL Watch metadata, occupancy and encrypted source credentials."""

import logging
from typing import Any, Sequence

from sqlalchemy import JSON, func, inspect, literal_column, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError

from openviking.resource.watch_store_postgres_schema import (
    POSTGRES_WATCH_INDEX_KEYS,
    POSTGRES_WATCH_TARGET_PREFIX_CHARS,
    postgres_watch_type_name,
)
from openviking.resource.watch_store_sql import SQLWatchStore
from openviking.storage.postgres_config import postgres_connection_options

logger = logging.getLogger(__name__)

_SCHEMA_SCRIPT = "openviking/service/sql/001_watch_tasks_postgres.sql"


class PostgresWatchStore(SQLWatchStore):
    dialect_name = "postgresql"
    target_prefix_index_chars = POSTGRES_WATCH_TARGET_PREFIX_CHARS

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        return getattr(exc.orig, "sqlstate", None) == "23505"

    @property
    def provider(self) -> str:
        return "postgres"

    @property
    def _target_key_expression(self) -> Any:
        # Keep index constants literal so prepared generic plans can match the DDL.
        return func.substr(
            self._tasks.c.target_uri_key,
            literal_column("1"),
            literal_column(str(POSTGRES_WATCH_TARGET_PREFIX_CHARS)),
        )

    def _schema_error(self, problem: str) -> RuntimeError:
        return RuntimeError(
            f"PostgreSQL Watch table {self._tasks.name!r} {problem}. "
            f"Please have an administrator provision or migrate it using {_SCHEMA_SCRIPT}; "
            "replace table and index names for storage.watch_store.params.table. "
            "Initialization does not create or migrate the schema."
        )

    async def _validate_schema(self, session: Any) -> None:
        try:
            await super()._validate_schema(session)
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if state not in {"42P01", "42703"}:
                raise
            problem = "does not exist" if state == "42P01" else "is missing required columns"
            raise self._schema_error(problem) from exc

        connection = await session.connection()

        def read_schema(sync_connection):
            inspector = inspect(sync_connection)
            return (
                inspector.get_columns(self._tasks.name),
                inspector.get_pk_constraint(self._tasks.name)["constrained_columns"],
            )

        columns, primary_key = await connection.run_sync(read_schema)
        actual = {column["name"]: column for column in columns}
        mismatches = []
        for column in self._tasks.c:
            expected_type = column.type.dialect_impl(connection.dialect)
            found = actual[column.name]
            # Driver adaptation can turn Text into a String implementation;
            # compile the declared type, as CREATE TABLE does.
            expected_name = postgres_watch_type_name(column.type.compile(connection.dialect))
            found_name = postgres_watch_type_name(found["type"].compile(connection.dialect))
            if expected_name != found_name:
                mismatches.append(
                    f"{column.name} type: expected {expected_name}, found {found_name}"
                )
            if column.nullable != found["nullable"]:
                mismatches.append(
                    f"{column.name} nullable: expected {column.nullable}, found {found['nullable']}"
                )
            expected_collation = getattr(expected_type, "collation", None)
            found_collation = getattr(found["type"], "collation", None)
            if expected_collation is not None and expected_collation != found_collation:
                mismatches.append(
                    f"{column.name} collation: expected {expected_collation!r}, "
                    f"found {found_collation!r}"
                )
        expected_pk = [column.name for column in self._tasks.primary_key]
        if primary_key != expected_pk:
            mismatches.append(f"primary key: expected {expected_pk}, found {primary_key}")
        if mismatches:
            raise self._schema_error("has incompatible schema: " + "; ".join(mismatches))

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
        missing = [
            f"ix_{self._tasks.name}_{suffix}"
            for suffix, keys in POSTGRES_WATCH_INDEX_KEYS.items()
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
        return postgres_connection_options(values, context="Watch store")

    def _public_payload(self, names: Sequence[str]) -> Any:
        args: list[Any] = []
        for name in names:
            args.extend([name, self._tasks.c.payload[name]])
        return func.json_build_object(*args, type_=JSON)
