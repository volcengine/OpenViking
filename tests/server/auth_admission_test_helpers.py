# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real database auth waiting and cancellation contracts shared by SQL dialects."""

import asyncio
import threading
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import text

from openviking.server.api_keys.key_mgr import APIKeyManager, generate_api_key
from openviking.server.app import create_app
from openviking.server.auth import resolve_identity
from openviking.server.auth.plugins.api_key import ApiKeyAuthPlugin
from openviking.server.config import ServerConfig
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfig,
    OpenVikingConfigSingleton,
)


async def _wait_for_runtime(store, predicate):
    async def check():
        return predicate()

    async def poll():
        while not await store._sql_runtime.run(check):
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), timeout=5)


async def assert_auth_wait_budget(store, stage, monkeypatch):
    """Exercise real checkout, production auth dependencies and HTTP error handling."""
    await store.load()
    budget = store._auth_wait_timeout
    held_connection = threading.Event()
    held_slot = threading.Event()
    release = threading.Event()
    tasks = []

    async def hold_connection():
        async with store._session_factory() as session:
            await session.execute(text("SELECT 1"))
            held_connection.set()
            while not release.is_set():
                await asyncio.sleep(0.005)

    async def hold_slot():
        async with store._auth_read_slot():
            held_slot.set()
            while not release.is_set():
                await asyncio.sleep(0.005)

    async def release_slot():
        await asyncio.sleep(budget * 0.6)
        slot_task.cancel()
        await asyncio.gather(slot_task, return_exceptions=True)

    key = generate_api_key("acme", "admin")
    try:
        await store.create_account_with_api_key("acme", "admin", key)
        # These HTTP contracts must not load the developer's local ov.conf.
        monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", OpenVikingConfig())
        app = create_app(ServerConfig(auth_mode="api_key", root_api_key="root"))
        app.state.api_key_manager = APIKeyManager("root", store)
        app.state.auth_plugin = ApiKeyAuthPlugin()

        @app.get("/auth-wait-test")
        async def identity(identity=Depends(resolve_identity)):
            return {"account_id": identity.account_id}

        if stage in {"connection", "shared", "pool_timeout"}:
            tasks.append(asyncio.create_task(store._sql_runtime.run(hold_connection)))
            assert await asyncio.to_thread(held_connection.wait, 5)
        if stage in {"admission", "shared"}:
            slot_task = asyncio.create_task(store._sql_runtime.run(hold_slot))
            tasks.append(slot_task)
            assert await asyncio.to_thread(held_slot.wait, 5)
        if stage == "pool_timeout":
            store._engine.pool._timeout = budget * 0.1

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with monkeypatch.context() as patch:
                session_type = store._sessions.class_
                original_connection = session_type.connection
                original_execute = session_type.execute
                original_slot = store._auth_read_slot
                auth_times = {}

                @asynccontextmanager
                async def timed_slot():
                    loop = asyncio.get_running_loop()
                    auth_times["start"] = loop.time()
                    try:
                        async with original_slot() as deadline:
                            yield deadline
                    finally:
                        auth_times["end"] = loop.time()

                patch.setattr(store, "_auth_read_slot", timed_slot)

                async def slow_connection(session, *args, **kwargs):
                    await asyncio.sleep(budget * 2)
                    return await original_connection(session, *args, **kwargs)

                async def slow_query(session, statement, parameters=None, **kwargs):
                    if parameters and "auth_lookup_digest" in parameters:
                        await asyncio.sleep(budget * 2)
                    return await original_execute(session, statement, parameters, **kwargs)

                if stage == "connect":
                    patch.setattr(session_type, "connection", slow_connection)
                if stage == "query":
                    patch.setattr(session_type, "execute", slow_query)
                request = asyncio.create_task(
                    client.get("/auth-wait-test", headers={"X-API-Key": key})
                )
                tasks.append(request)
                if stage == "shared":
                    await _wait_for_runtime(store, lambda: bool(store._auth_admission[1]._waiters))
                    tasks.append(asyncio.create_task(release_slot()))
                response = await asyncio.wait_for(request, timeout=5)
                # Measure the SQL runtime operation, excluding HTTP setup/logging.
                elapsed = auth_times["end"] - auth_times["start"]
                if stage == "query":
                    assert response.status_code == 200, response.text
                    assert elapsed >= budget * 2
                else:
                    assert response.status_code == 503, response.text
                    assert response.json()["error"]["code"] == "UNAVAILABLE"
                    # A second full budget for checkout would take >= 1.6 * budget.
                    assert elapsed < budget * 1.4
                    if stage != "pool_timeout":
                        assert elapsed >= budget * 0.9
                    reason = response.json()["error"]["details"]["reason"]
                    assert ("admission" if stage == "admission" else "connection") in reason

            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)
            response = await client.get("/auth-wait-test", headers={"X-API-Key": key})
            assert response.status_code == 200, response.text
            assert response.json() == {"account_id": "acme"}
            invalid = await client.get(
                "/auth-wait-test", headers={"X-API-Key": generate_api_key("acme", "admin")}
            )
            assert invalid.status_code == 401, invalid.text
            if store._auth_admission is not None:
                assert store._auth_admission[1]._value == 1
                assert not store._auth_admission[1]._waiters
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await store.close()


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

    holder = first = second = None
    try:
        key = "admission-live-key"
        await store.create_account_with_api_key("acme", "admin", key)
        holder = asyncio.create_task(store._sql_runtime.run(hold_connection))
        assert await asyncio.to_thread(held.wait, 5)
        first = asyncio.create_task(store.get_auth_snapshot(key))
        await _wait_for_runtime(
            store, lambda: store._auth_admission is not None and store._auth_admission[1].locked()
        )
        second = asyncio.create_task(store.get_auth_snapshot(key))
        await _wait_for_runtime(store, lambda: bool(store._auth_admission[1]._waiters))
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
