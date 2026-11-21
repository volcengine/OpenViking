# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Direct deletion reads must return pooled connections after cancellation."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from openviking.server.account_stores.postgres import PostgresAccountStore
from tests.server.test_postgres_account_store import pg_database

__all__ = ["pg_database"]


async def test_deletion_cancel_and_pre_ping_restore_pool(pg_database, monkeypatch):
    params, admin, _, role = pg_database
    store = PostgresAccountStore(
        service_name="deletion-cleanup-" + uuid4().hex, params={**params, "maxsize": 1}
    )
    await store.load()
    pending = None
    try:
        execute = AsyncConnection.execute

        async def slow(connection, statement, *args, **kwargs):
            return await execute(connection, text("SELECT pg_sleep(30)"))

        with monkeypatch.context() as patch:
            patch.setattr(AsyncConnection, "execute", slow)
            pending = asyncio.create_task(store.get_deletion("missing", "alice"))

            async def started():
                while not admin.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE usename = %s AND state = 'active' AND query = 'SELECT pg_sleep(30)')",
                    (role,),
                ).fetchone()[0]:
                    if pending.done():
                        await pending
                        raise AssertionError("read finished before the cancellation probe")
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(started(), 5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 5)
        assert await asyncio.wait_for(store.get_deletion("missing", "alice"), 5) is None

        async def backend():
            async with store._engine.connect() as connection:
                return await connection.scalar(text("SELECT pg_backend_pid()"))

        pid = await store._sql_runtime.run(backend)
        assert admin.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0]
        assert await asyncio.wait_for(store.get_deletion("missing"), 5) is None
        await store.create_account("committed", "admin")
        assert await store.get_account("committed") is not None
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await store.close()
