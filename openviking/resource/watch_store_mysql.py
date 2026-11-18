# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MySQL provider for the shared transactional Watch store."""

from typing import Any

from sqlalchemy.exc import IntegrityError

from openviking.resource.watch_store_sql import (
    SQLWatchStore,
    watch_tables,  # noqa: F401 - preserve the schema helper import
)
from openviking.service.task_store_mysql import MySQLTaskWorkStore


class MySQLWatchStore(SQLWatchStore):
    dialect_name = "mysql"

    @staticmethod
    def _connection_options(values: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        return MySQLTaskWorkStore._connection_options(values)

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        return MySQLTaskWorkStore._is_duplicate_error(exc)
