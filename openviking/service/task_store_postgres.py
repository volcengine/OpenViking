# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL connection settings for the transactional Task/Work store."""

from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

from openviking.service.task_store_sql import SQLTaskWorkStore
from openviking.storage.postgres_config import postgres_connection_options


class PostgresTaskWorkStore(SQLTaskWorkStore):
    """Shared durable Task/Work storage; no Redis or pgvector required."""

    provider_name = "PostgreSQL"
    dependency_extra = "postgres"
    dialect_name = "postgresql"

    @staticmethod
    def _connection_options(values: dict[str, object]) -> tuple[URL, dict[str, object]]:
        url, options = postgres_connection_options(values, context="task store")
        options["connect_args"]["options"] = "-c timezone=UTC"
        return url, options

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        return getattr(exc.orig, "sqlstate", None) == "23505"
