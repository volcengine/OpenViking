# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real MySQL regression contracts for both account-store implementations.

Set OV_TEST_MYSQL to connection JSON for an isolated, pre-provisioned database.
OV_TEST_MYSQL_ACCOUNT_RUNTIME optionally selects a DML-only runtime account.
These tests also run against the implementation before relational extraction.
"""

import asyncio
import base64
import json
import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy import text

from openviking.server.account_stores.mysql import MySQLAccountStore
from openviking.server.account_stores.mysql_schema import Account
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import (
    AlreadyExistsError,
    FailedPreconditionError,
    NotFoundError,
    UnauthenticatedError,
)


@pytest.fixture
def mysql_params():
    raw = os.environ.get("OV_TEST_MYSQL")
    if not raw:
        pytest.skip("Set OV_TEST_MYSQL for real MySQL account-store regressions")
    pytest.importorskip("aiomysql")
    return json.loads(raw)


@pytest.fixture
async def mysql_accounts(mysql_params):
    runtime_params = json.loads(
        os.environ.get("OV_TEST_MYSQL_ACCOUNT_RUNTIME", json.dumps(mysql_params))
    )
    service = "test-" + uuid4().hex
    managers = []

    async def make(*, mode="hash", service_name=service):
        manager = build_api_key_manager(
            "root",
            None,
            service_name=service_name,
            account_store_provider="mysql",
            account_store_params={
                **runtime_params,
                "credential_storage": mode,
                "credential_encryption_key": base64.b64encode(b"x" * 32).decode(),
                "maxsize": 2,
            },
        )
        managers.append(manager)
        await manager.load()
        return manager

    try:
        yield make
    finally:
        for manager in reversed(managers):
            await manager.close()
        pymysql = pytest.importorskip("pymysql")
        with pymysql.connect(**mysql_params, autocommit=True) as connection:
            with connection.cursor() as cursor:
                for name in {manager._store._service_name for manager in managers}:
                    cursor.execute("DELETE FROM ov_accounts WHERE service_name=%s", (name,))


def _runtime(store):
    return getattr(store, "_sql_runtime", None) or store._mysql_runtime


async def test_mysql_preprovisioned_low_privilege_startup_and_reload(mysql_accounts, mysql_params):
    manager = await mysql_accounts()
    peer = await mysql_accounts()
    assert [a["account_id"] for a in await manager.list_accounts()] == ["default"]
    key = await manager.create_account("acme", "admin")
    await manager.close()
    await asyncio.gather(manager.load(), manager.load(), peer.load())
    assert (await peer.resolve_identity(key)).user_id == "admin"
    assert (await manager.get_account("acme"))["user_count"] == 1
    raw_runtime = os.environ.get("OV_TEST_MYSQL_ACCOUNT_RUNTIME")
    if raw_runtime:
        import pymysql

        with pymysql.connect(**json.loads(raw_runtime), autocommit=True) as connection:
            with connection.cursor() as cursor:
                with pytest.raises(pymysql.err.OperationalError) as denied:
                    cursor.execute("CREATE TABLE forbidden_test_table (id BIGINT)")
                assert denied.value.args[0] == 1142


@pytest.mark.parametrize("mode", ["hash", "encrypted"])
async def test_mysql_credentials_groups_conditional_rotation_and_service_isolation(
    mysql_accounts, mysql_params, mode
):
    writer = await mysql_accounts(mode=mode)
    peer = await mysql_accounts(mode=mode)
    other = await mysql_accounts(mode=mode, service_name="other-" + uuid4().hex)
    await writer.create_account("acme", "admin")
    key = await writer.register_user("acme", "alice")
    await other.create_account("acme", "admin")
    with pytest.raises(UnauthenticatedError):
        await other.resolve_identity(key)
    await writer.create_group("acme", "team")
    await writer.add_group_members("acme", "team", ["alice", "alice"])
    await writer.add_group_members("acme", "team", ["alice"])
    assert await peer.get_group_members("acme", "team") == ["alice"]
    assert (await peer.resolve_identity(key)).group_ids == ("team",)
    await writer.set_role("acme", "alice", "admin")
    assert (await peer.resolve_identity(key)).role == "admin"
    page = await peer.list_users_page("acme", name_filter="ali*", limit=1)
    assert (page["total"], page["account_total"], page["manager_count"], page["key_count"]) == (
        1,
        2,
        2,
        2,
    )
    assert ("api_key" in page["users"][0]) == (mode == "encrypted")
    import pymysql

    with pymysql.connect(**mysql_params, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT material FROM ov_accounts_credentials WHERE service_name=%s",
                (writer._store._service_name,),
            )
            assert all(key not in material for (material,) in cursor.fetchall())
    new_key = await writer.regenerate_key("acme", "alice", expected_api_key=key)
    with pytest.raises(UnauthenticatedError):
        await peer.resolve_identity(key)
    with pytest.raises(UnauthenticatedError):
        await writer.regenerate_key("acme", "alice", expected_api_key=key)
    assert (await peer.resolve_identity(new_key)).user_id == "alice"
    await writer.remove_group_members("acme", "team", ["alice"])
    await writer.delete_group("acme", "team")
    assert (await peer.resolve_identity(new_key)).group_ids == ()
    await writer._store.revoke_active_api_keys("acme", "alice")
    with pytest.raises(UnauthenticatedError):
        await peer.resolve_identity(new_key)


async def test_mysql_credential_history_and_revocation_timestamps(mysql_accounts, mysql_params):
    manager = await mysql_accounts()
    first_key = await manager.create_account("acme", "admin")
    for _ in range(12):
        key = await manager.regenerate_key("acme", "admin")
    with pytest.raises(UnauthenticatedError):
        await manager.resolve_identity(first_key)
    assert (await manager.resolve_identity(key)).user_id == "admin"
    import pymysql

    with pymysql.connect(**mysql_params, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT revoked, revoked_at, active_user_pk FROM ov_accounts_credentials "
                "WHERE service_name=%s",
                (manager._store._service_name,),
            )
            rows = cursor.fetchall()
    assert len(rows) == 11 and sum(revoked for revoked, _, _ in rows) == 10
    assert all(at is not None and active is None for revoked, at, active in rows if revoked)


async def test_mysql_concurrent_role_changes_preserve_last_admin(mysql_accounts):
    manager = await mysql_accounts()
    peer = await mysql_accounts()
    await manager.create_account("acme", "admin")
    await manager.register_user("acme", "alice", role="admin")
    results = await asyncio.gather(
        manager.set_role("acme", "admin", "user"),
        peer.set_role("acme", "alice", "user"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, FailedPreconditionError) for result in results) == 1
    assert (await manager.list_users_page("acme"))["manager_count"] == 1


async def test_mysql_deletion_fences_cas_and_foreign_key_cascades(mysql_accounts, mysql_params):
    manager = await mysql_accounts()
    await manager.create_account("acme", "admin")
    key = await manager.register_user("acme", "alice")
    await manager.create_group("acme", "team")
    await manager.add_group_members("acme", "team", ["alice"])
    owner = {"owner_account_id": "acme", "owner_user_id": "admin"}
    with pytest.raises(FailedPreconditionError):
        await manager.begin_deletion("acme", "admin", task_id="last-admin", **owner)
    await manager.begin_deletion("acme", "alice", task_id="task1", **owner)
    with pytest.raises(UnauthenticatedError):
        await manager.resolve_identity(key)
    await manager.replace_deletion_task(
        "acme", "alice", expected_task_id="task1", task_id="task2", **owner
    )
    assert not await manager.finish_deletion("acme", "alice", "task1")
    assert await manager.finish_deletion("acme", "alice", "task2")
    assert await manager.get_group_members("acme", "team") == []
    await manager.begin_deletion("acme", None, task_id="account-task", **owner)
    with pytest.raises(FailedPreconditionError):
        await manager.register_user("acme", "bob")
    assert await manager.finish_deletion("acme", None, "account-task")
    import pymysql

    with pymysql.connect(**mysql_params, autocommit=True) as connection:
        with connection.cursor() as cursor:
            for name in (
                "ov_accounts_users",
                "ov_accounts_groups",
                "ov_accounts_credentials",
                "ov_accounts_group_members",
            ):
                cursor.execute(
                    f"SELECT count(*) FROM {name} WHERE service_name=%s",
                    (manager._store._service_name,),
                )
                assert cursor.fetchone()[0] == 0


async def test_mysql_duplicate_writes_and_group_batches_roll_back(mysql_accounts):
    manager = await mysql_accounts()
    await manager.create_account("acme", "admin")
    await manager.register_user("acme", "alice")
    with pytest.raises(AlreadyExistsError):
        await manager.create_account("acme", "other")
    assert await manager.get_user("acme", "other") is None
    await manager.create_group("acme", "team")
    with pytest.raises(NotFoundError):
        await manager.add_group_members("acme", "team", ["alice", "missing"])
    assert await manager.get_group_members("acme", "team") == []
    assert (await manager.get_account("acme"))["user_count"] == 2


async def test_mysql_concurrent_trusted_identity_bootstrap(mysql_accounts):
    manager, peer = await mysql_accounts(), await mysql_accounts()
    results = await asyncio.gather(
        manager.ensure_trusted_identities({"trusted": {"alice", "bob"}}),
        peer.ensure_trusted_identities({"trusted": {"alice", "bob"}}),
    )
    assert sum(result["created_accounts"] for result in results) == 1
    assert sum(result["created_users"] for result in results) == 2
    assert (await manager.list_users_page("trusted"))["account_total"] == 2


async def test_mysql_cancelled_transaction_rolls_back_before_pool_reuse(mysql_accounts):
    manager = await mysql_accounts()
    store = manager._store
    started = threading.Event()

    async def write():
        async with store._transaction() as session:
            session.add(Account(service_name=store._service_name, account_id="cancelled"))
            await session.flush()
            started.set()
            await session.execute(text("SELECT SLEEP(30)"))

    task = asyncio.create_task(_runtime(store).run(write))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await store.get_account("cancelled") is None
    await manager.create_account("survived", "admin")
    assert await store.get_account("survived") is not None


async def test_mysql_case_sensitive_identifier_lookup_and_pagination(mysql_accounts):
    manager = await mysql_accounts()
    await manager.create_account("Acme", "admin")
    await manager.create_account("acme", "admin")
    await manager.register_user("Acme", "Alice")
    await manager.register_user("Acme", "alice")
    page = await manager.list_users_page("Acme", name_filter="A*", limit=1)
    assert page["total"] == 1 and page["users"][0]["user_id"] == "Alice"
    assert (await manager.get_account("Acme"))["user_count"] == 3
    assert (await manager.get_account("acme"))["user_count"] == 1
    assert (await manager.get_account("Acme"))["created_at"].endswith("+00:00")


def test_mysql_account_runtime_across_caller_event_loops(mysql_params):
    store = MySQLAccountStore(service_name="test-" + uuid4().hex, params=mysql_params)
    try:
        asyncio.run(store.load())
        asyncio.run(store.create_account("acme", "admin"))
        assert asyncio.run(store.get_account("acme"))["user_count"] == 1
        asyncio.run(store.close())
        asyncio.run(store.load())
        assert asyncio.run(store.get_account("acme"))["user_count"] == 1
    finally:
        asyncio.run(store.close())
        import pymysql

        with pymysql.connect(**mysql_params, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM ov_accounts WHERE service_name=%s", (store._service_name,)
                )
