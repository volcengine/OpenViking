# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cancellation during a real read must return the connection before reuse."""

import asyncio
import threading

import pytest
from sqlalchemy import event


async def assert_auth_cancelled_query_returns_connection(store, sleep_query, query_running):
    await store.load()
    started = threading.Event()

    def delay_auth(conn, cursor, statement, parameters, context, executemany):
        if "ov_accounts_credentials" in statement and "lookup_digest" in statement:
            started.set()
            return sleep_query, {}
        return statement, parameters

    engine = store._engine.sync_engine
    task = None
    try:
        key = "connection-live-key"
        await store.create_account_with_api_key("acme", "admin", key)
        event.listen(engine, "before_cursor_execute", delay_auth, retval=True)
        task = asyncio.create_task(store.get_auth_snapshot(key))
        assert await asyncio.to_thread(started.wait, 5)

        async def wait_until_executing():
            while not await asyncio.to_thread(query_running):
                await asyncio.sleep(0.01)

        # A client-side execute hook can fire before the server receives SQL.
        await asyncio.wait_for(wait_until_executing(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)

        async def checkedout():
            return store._engine.sync_engine.pool.checkedout()

        assert await store._sql_runtime.run(checkedout) == 0
        event.remove(engine, "before_cursor_execute", delay_auth)
        snapshot = await asyncio.wait_for(store.get_auth_snapshot(key), timeout=5)
        assert snapshot["account_id"] == "acme"
        assert await store._sql_runtime.run(checkedout) == 0
        await store.create_account("survived", "admin")
        assert await store.get_account("survived") is not None
        await store.close()
        await store.load()
        assert await store.get_auth_snapshot(key) == snapshot
    finally:
        if event.contains(engine, "before_cursor_execute", delay_auth):
            event.remove(engine, "before_cursor_execute", delay_auth)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await store.close()
