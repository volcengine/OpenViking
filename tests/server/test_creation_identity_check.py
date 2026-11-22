# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real PostgreSQL identity checks and Task creation before/after fencing."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, insert, text
from sqlalchemy.ext.asyncio import AsyncConnection

from openviking.server.account_stores.mysql import MySQLAccountStore
from openviking.server.account_stores.postgres import PostgresAccountStore
from openviking.server.account_stores.sql_schema import Account
from openviking.server.api_keys.key_mgr import APIKeyManager
from openviking.service.deletion import DeletionService
from openviking.service.task_store_postgres import PostgresTaskWorkStore
from openviking.service.task_tracker import TaskTracker
from openviking_cli.exceptions import FailedPreconditionError
from tests.server.test_postgres_account_store import pg_database as pg_database
from tests.service.test_task_work_contract import OWNER
from tests.service.test_task_work_contract import task_stores as task_stores


async def test_creation_identity_unloaded():
    store = PostgresAccountStore(
        service_name="identity19", params={"user": "unused", "database": "unused"}
    )
    with pytest.raises(AssertionError):
        await store.is_creation_blocked("missing", "missing")


async def test_creation_identity_manager_and_old_manager_fallback():
    store = SimpleNamespace(
        get_deletion=AsyncMock(return_value=None),
        get_user=AsyncMock(return_value={"user_id": "alice"}),
    )
    manager = APIKeyManager("root", store)
    guard = DeletionService(
        service=SimpleNamespace(), manager=manager, service_loop=asyncio.get_running_loop()
    )
    assert not await guard._creation_blocked("acme", "alice")
    store.get_deletion.assert_awaited_once_with("acme", "alice")
    store.get_user.assert_awaited_once_with("acme", "alice")
    store.get_deletion.return_value = {"task_id": "deleting"}
    store.get_user.reset_mock()
    assert await guard._creation_blocked("acme", "alice")
    store.get_user.assert_not_awaited()
    old_manager = SimpleNamespace(
        get_deletion=AsyncMock(return_value=None), get_user=AsyncMock(return_value=None)
    )
    old_guard = DeletionService(
        service=SimpleNamespace(), manager=old_manager, service_loop=asyncio.get_running_loop()
    )
    assert await old_guard._creation_blocked("acme", "missing")
    old_manager.get_user.assert_awaited_once_with("acme", "missing")


async def test_creation_identity_mysql_fallback(monkeypatch):
    store = MySQLAccountStore(
        service_name="fallback", params={"user": "ov", "password": "", "database": "ov"}
    )
    deletion = AsyncMock(return_value=None)
    user = AsyncMock(return_value={"user_id": "alice"})
    monkeypatch.setattr(store, "get_deletion", deletion)
    monkeypatch.setattr(store, "get_user", user)
    # Run inside the private loop so the SQL runtime wrapper need not connect.
    assert not await store._sql_runtime.run(lambda: store.is_creation_blocked("acme", "alice"))
    deletion.assert_awaited_once_with("acme", "alice")
    user.assert_awaited_once_with("acme", "alice")
    deletion.return_value = {"task_id": "deleting"}
    user.reset_mock()
    assert await store._sql_runtime.run(lambda: store.is_creation_blocked("acme", "alice"))
    user.assert_not_awaited()
    deletion.return_value = None
    user.return_value = None
    assert await store._sql_runtime.run(lambda: store.is_creation_blocked("acme", "missing"))


async def test_creation_identity_live_services_registration_and_deletion(pg_database):
    params, _, _, _ = pg_database
    service = "identity19-" + uuid4().hex
    writer = PostgresAccountStore(service_name=service, params=params)
    reader = PostgresAccountStore(service_name=service, params=params)
    other = PostgresAccountStore(service_name=service + "-other", params=params)
    for store in (writer, reader, other):
        await store.load()
    try:
        await writer.create_account("acme", "admin")
        await writer.create_user("acme", "alice", "user")
        await writer.create_account("second", "admin")
        for _ in range(2):
            assert not await reader.is_creation_blocked("acme", "alice")
            assert not await reader.is_creation_blocked("second", "admin")
            assert await other.is_creation_blocked("acme", "alice")
            for account, user in (
                ("acme", "missing"),
                ("missing", "alice"),
                ("acme", "Alice"),
                ("acme", "' OR 1=1 --"),
                ("acme' OR 1=1 --", "alice"),
                (None, "alice"),
                ("acme", None),
            ):
                assert await reader.is_creation_blocked(account, user)
        manager = APIKeyManager("root", reader)
        guard = DeletionService(
            service=SimpleNamespace(), manager=manager, service_loop=asyncio.get_running_loop()
        )
        assert not await asyncio.to_thread(
            lambda: asyncio.run(guard._creation_blocked("acme", "alice"))
        )
        owner = {"owner_account_id": "acme", "owner_user_id": "admin"}
        await writer.begin_deletion("acme", "alice", task_id="identity19-user", **owner)
        assert await reader.is_creation_blocked("acme", "alice")
        assert not await reader.is_creation_blocked("acme", "admin")
        assert await writer.finish_deletion("acme", "alice", "identity19-user")
        assert await reader.is_creation_blocked("acme", "alice")
        await writer.create_user("acme", "alice", "user")
        assert not await reader.is_creation_blocked("acme", "alice")
        await writer.begin_deletion("acme", None, task_id="identity19-account", **owner)
        assert await reader.is_creation_blocked("acme", "alice")
        assert await reader.is_creation_blocked("acme", "admin")
        assert await writer.finish_deletion("acme", None, "identity19-account")
        assert await reader.is_creation_blocked("acme", "admin")
        await reader.close()
        await reader.load()
        assert not await reader.is_creation_blocked("second", "admin")
    finally:
        for store in (other, reader, writer):
            await store.close()


async def test_creation_identity_task_postcommit_fence_and_withdrawal(
    pg_database, task_stores, monkeypatch
):
    task_store, peer = task_stores
    if not isinstance(task_store, PostgresTaskWorkStore):
        pytest.skip("PostgreSQL Task persistence and identity-fence race")
    params, _, _, _ = pg_database
    accounts = PostgresAccountStore(service_name="identity19-race-" + uuid4().hex, params=params)
    await accounts.load()
    try:
        await accounts.create_account(OWNER["account_id"], "admin19")
        await accounts.create_user(OWNER["account_id"], OWNER["user_id"], "user")
        manager = APIKeyManager("root", accounts)
        guard = DeletionService(
            service=SimpleNamespace(), manager=manager, service_loop=asyncio.get_running_loop()
        )
        tracker = TaskTracker(task_store)
        tracker.set_deletion_guard(guard._creation_blocked)
        queries = []

        def count_check(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("SELECT") and "creation_user_id" in statement:
                queries.append(statement)

        event.listen(accounts._engine.sync_engine, "before_cursor_execute", count_check)
        try:
            await tracker.create("content_write", "allowed", task_id="identity19-allowed", **OWNER)
            assert len(queries) == 2  # Both pre-persistence and post-persistence checks remain.
            original_create = task_store.create

            async def persist_then_fence(task, **kwargs):
                committed = await original_create(task, **kwargs)
                await accounts.begin_deletion(
                    OWNER["account_id"],
                    OWNER["user_id"],
                    task_id="identity19-fence",
                    owner_account_id=OWNER["account_id"],
                    owner_user_id="admin19",
                )
                return committed

            monkeypatch.setattr(task_store, "create", persist_then_fence)
            queries.clear()
            with pytest.raises(FailedPreconditionError, match="Identity deletion"):
                await tracker.create("content_write", "raced", task_id="identity19-raced", **OWNER)
            assert len(queries) == 2
            assert await peer.get_task("identity19-raced", **OWNER) is None
            create = AsyncMock()
            monkeypatch.setattr(task_store, "create", create)
            queries.clear()
            with pytest.raises(FailedPreconditionError, match="Identity deletion"):
                await tracker.create(
                    "content_write", "blocked", task_id="identity19-blocked", **OWNER
                )
            assert len(queries) == 1
            create.assert_not_awaited()
        finally:
            event.remove(accounts._engine.sync_engine, "before_cursor_execute", count_check)
    finally:
        await accounts.close()


async def test_creation_identity_pool_rollback_cancel_and_disconnect(pg_database, monkeypatch):
    params, _, _, _ = pg_database
    service = "identity19-pool-" + uuid4().hex
    writer = PostgresAccountStore(service_name=service, params=params)
    reader = PostgresAccountStore(service_name=service, params={**params, "maxsize": 1})
    await writer.load()
    await reader.load()
    observed = []

    def inspect(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "creation_user_id" in statement:
            observed.append(conn.connection.dbapi_connection.autocommit)

    event.listen(reader._engine.sync_engine, "before_cursor_execute", inspect)
    try:
        await writer.create_account("acme", "admin")
        assert not await reader.is_creation_blocked("acme", "admin")
        assert observed == [True]

        async def rollback():
            async with reader._transaction() as session:
                connection = await session.connection()
                raw = await connection.get_raw_connection()
                assert raw.driver_connection.autocommit is False
                await session.execute(
                    insert(Account).values(service_name=service, account_id="aborted")
                )
                raise RuntimeError("identity19 rollback")

        with pytest.raises(RuntimeError, match="identity19 rollback"):
            await reader._sql_runtime.run(rollback)
        assert await writer.get_account("aborted") is None
        execute = AsyncConnection.execute
        entered = threading.Event()

        async def slow(self, statement, *args, **kwargs):
            entered.set()
            return await execute(self, text("SELECT pg_sleep(30)"))

        with monkeypatch.context() as patch:
            patch.setattr(AsyncConnection, "execute", slow)
            pending = asyncio.create_task(reader.is_creation_blocked("acme", "admin"))
            assert await asyncio.to_thread(entered.wait, 5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        assert not await asyncio.wait_for(reader.is_creation_blocked("acme", "admin"), 5)

        async def backend_id():
            async with reader._engine.connect() as connection:
                return await connection.scalar(text("SELECT pg_backend_pid()"))

        pid = await reader._sql_runtime.run(backend_id)

        async def terminate():
            async with writer._engine.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                return await connection.scalar(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                )

        assert await writer._sql_runtime.run(terminate)
        assert not await asyncio.wait_for(reader.is_creation_blocked("acme", "admin"), 5)
        await reader.create_user("acme", "committed", "user")
        assert not await writer.is_creation_blocked("acme", "committed")
    finally:
        event.remove(reader._engine.sync_engine, "before_cursor_execute", inspect)
        await reader.close()
        await writer.close()
