# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MySQL 5.7 adapter for Compile persistence."""

from sqlalchemy.exc import DBAPIError, IntegrityError

from openviking.service.compile_store_sql import SQLCompileStore
from openviking.storage.mysql_config import mysql_connection_options


class MySQLCompileStore(SQLCompileStore):
    dialect = dependency = "mysql"
    init_sql = "compile_store_init.sql"
    _connection_options = staticmethod(mysql_connection_options)

    @staticmethod
    def _is_duplicate(exc: IntegrityError) -> bool:
        return getattr(exc.orig, "args", ())[:1] == (1062,)

    @staticmethod
    def _is_schema_error(exc: DBAPIError) -> bool:
        return getattr(exc.orig, "args", ())[:1] in {(1054,), (1146,)}
