# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL adapter for Compile persistence."""

from sqlalchemy.exc import DBAPIError, IntegrityError

from openviking.service.compile_store_sql import SQLCompileStore
from openviking.storage.postgres_config import postgres_connection_options


class PostgresCompileStore(SQLCompileStore):
    dialect = "postgresql"
    dependency = "postgres"
    init_sql = "compile_store_postgres_init.sql"

    @staticmethod
    def _connection_options(options):
        return postgres_connection_options(options, context="Compile store")

    @staticmethod
    def _is_duplicate(exc: IntegrityError) -> bool:
        return getattr(exc.orig, "sqlstate", None) == "23505"

    @staticmethod
    def _is_schema_error(exc: DBAPIError) -> bool:
        return getattr(exc.orig, "sqlstate", None) in {"42P01", "42703"}
