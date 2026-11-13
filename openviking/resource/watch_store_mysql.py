# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MySQL connection and JSON projection for the shared Watch store."""

from typing import Any, Sequence

from sqlalchemy import JSON, func

from openviking.resource.watch_store_sql import SQLWatchStore


class MySQLWatchStore(SQLWatchStore):
    @property
    def provider(self) -> str:
        return "mysql"

    @staticmethod
    def _connection_options(values: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        from openviking.service.task_store_mysql import MySQLTaskWorkStore

        return MySQLTaskWorkStore._connection_options(values)

    def _public_payload(self, names: Sequence[str]) -> Any:
        args: list[Any] = []
        for name in names:
            args.extend([name, func.json_extract(self._tasks.c.payload, f"$.{name}")])
        return func.json_object(*args, type_=JSON)
