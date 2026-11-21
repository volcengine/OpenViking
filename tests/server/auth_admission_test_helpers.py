# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real database cancellation contracts shared by the relational dialects."""

import asyncio
import threading

import pytest
from sqlalchemy import text


async def assert_auth_admission_cancellation_and_reload(store):
    await store.load()
    held = threading.Event()
    release = threading.Event()

    async def hold_connection():
        async with store._session_factory() as session:
            await session.execute(text("SELECT 1"))
            held.set()
            while not release.is_set():
                await asyncio.sleep(0.005)

    async def wait_for_runtime(predicate):
        async def check():
            return predicate()

        async def poll():
            while not await store._sql_runtime.run(check):
                await asyncio.sleep(0.005)

        await asyncio.wait_for(poll(), timeout=5)

    holder = first = second = None
    try:
        key = "admission-live-key"
        await store.create_account_with_api_key("acme", "admin", key)
        holder = asyncio.create_task(store._sql_runtime.run(hold_connection))
        assert await asyncio.to_thread(held.wait, 5)
        first = asyncio.create_task(store.get_auth_snapshot(key))
        await wait_for_runtime(
            lambda: store._auth_admission is not None and store._auth_admission[1].locked()
        )
        second = asyncio.create_task(store.get_auth_snapshot(key))
        await wait_for_runtime(lambda: bool(store._auth_admission[1]._waiters))
        # Cancel both a queued permit waiter and an admitted connection waiter.
        for task in (second, first):
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        release.set()
        await asyncio.wait_for(holder, timeout=5)
        snapshot = await asyncio.wait_for(store.get_auth_snapshot(key), timeout=5)
        assert snapshot["account_id"] == "acme"
        state = store._auth_admission
        assert state[1]._value == 1
        await store.close()
        assert store._auth_admission is None
        await store.load()
        assert await asyncio.wait_for(store.get_auth_snapshot(key), timeout=5) == snapshot
        assert store._auth_admission is not state
    finally:
        release.set()
        tasks = [task for task in (holder, first, second) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await store.close()
