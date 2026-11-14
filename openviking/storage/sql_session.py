# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLAlchemy session cleanup for the shared relational runtime."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.storage.sql_runtime import wait_sql_cleanup


class SQLAsyncSession(AsyncSession):
    async def __aexit__(self, type_: Any, value: Any, traceback: Any) -> None:
        await wait_sql_cleanup(self.close())
