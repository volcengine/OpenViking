# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MySQL connection settings for the transactional Task/Work store."""

from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

from openviking.service.task_store_sql import SQLTaskWorkStore
from openviking.storage.mysql_config import mysql_connection_options


class MySQLTaskWorkStore(SQLTaskWorkStore):
    provider_name = "MySQL"
    dependency_extra = "mysql"
    dialect_name = "mysql"

    @staticmethod
    def _connection_options(values: dict[str, object]) -> tuple[URL, dict[str, object]]:
        return mysql_connection_options(values)

    @staticmethod
    def _is_duplicate_error(exc: IntegrityError) -> bool:
        args = getattr(getattr(exc, "orig", None), "args", ())
        return bool(args and args[0] == 1062)
