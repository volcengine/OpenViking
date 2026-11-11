# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLAlchemy session cleanup for the shared MySQL runtime."""

from typing import Any

from sqlalchemy import event
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from openviking_cli.utils import get_logger

from openviking.storage.mysql_runtime import wait_mysql_cleanup

logger = get_logger(__name__)


def require_writable_checkout(connection, record, proxy) -> None:
    """HA写入连接在事务开始前校验主角色；不重放已执行的SQL或业务任务。"""
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT @@global.read_only")
        row = cursor.fetchone()
    finally:
        cursor.close()
    connection.rollback()
    if row is None or row[0] != 0:
        # 交给SQLAlchemy原生失效/有界重连，不把旧主可达误当作可写。
        logger.warning("MySQL pool rejected a connection to a read-only server")
        raise DisconnectionError("MySQL connection is not writable")


def create_mysql_engine(url, *, require_writable=False, **options):
    """复用原生连接池；私有化HA按配置启用事务前主角色校验。"""
    engine = create_async_engine(url, **options)
    if require_writable:
        event.listen(engine.sync_engine, "checkout", require_writable_checkout)
    return engine


class MySQLAsyncSession(AsyncSession):
    async def __aexit__(self, type_: Any, value: Any, traceback: Any) -> None:
        await wait_mysql_cleanup(self.close())
