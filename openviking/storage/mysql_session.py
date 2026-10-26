# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLAlchemy session cleanup for the shared MySQL runtime."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from openviking.storage.mysql_runtime import wait_mysql_cleanup


class MySQLAsyncSession(AsyncSession):
    async def __aexit__(self, type_: Any, value: Any, traceback: Any) -> None:
        await wait_mysql_cleanup(self.close())
