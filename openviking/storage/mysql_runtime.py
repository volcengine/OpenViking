# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility exports for MySQL stores using the shared SQL runtime."""

from openviking.storage.sql_runtime import (
    SQLRuntime as MySQLRuntime,
)
from openviking.storage.sql_runtime import (
    get_shared_sql_runtime as get_shared_mysql_runtime,
)
from openviking.storage.sql_runtime import (
    sql_runtime_store,
)
from openviking.storage.sql_runtime import (
    wait_sql_cleanup as wait_mysql_cleanup,
)

__all__ = [
    "MySQLRuntime",
    "get_shared_mysql_runtime",
    "mysql_runtime_store",
    "wait_mysql_cleanup",
]


def mysql_runtime_store(cls):
    return sql_runtime_store(
        cls,
        runtime_attribute="_mysql_runtime",
        lifecycle_attribute="_mysql_lifecycle_lock",
    )
