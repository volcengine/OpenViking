# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PG integration: set OV_TEST_POSTGRES to administrator connection JSON.

The configured role must be able to create a disposable schema and login role.
Tests use the shipped SQL and a separate DML-only runtime role, and remove both.
Example: {"host":"127.0.0.1","port":5432,"user":"postgres","database":"postgres"}
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateTable

from openviking.server.account_stores.mysql import MySQLAccountStore
from openviking.server.account_stores.postgres import PostgresAccountStore
from openviking.server.account_stores.sql_schema import Account, Base
from openviking.server.config import AccountStoreConfig
from openviking.server.identity import Role
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import (
    AlreadyExistsError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    UnauthenticatedError,
)


def test_postgres_configuration_and_mysql_dialect_compatibility():
    assert AccountStoreConfig(provider=" POSTGRES ").provider == "postgres"
    with pytest.raises(InvalidArgumentError, match="service_name"):
        build_api_key_manager("root", None, account_store_provider="postgres")
    store = PostgresAccountStore(service_name="test", params={"user": "ov", "database": "ov"})
    assert store._url.drivername == "postgresql+psycopg"
    assert store._engine_options["pool_size"] == 10
    for table in Base.metadata.sorted_tables:
        pg_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        mysql_ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
        assert "utf8mb4_bin" not in pg_ddl and "ascii_bin" not in pg_ddl
        assert '"C"' not in mysql_ddl
        assert "utf8mb4_bin" in mysql_ddl
        if "created_at" in table.c:
            assert "TIMESTAMP WITH TIME ZONE" in pg_ddl
            assert "DATETIME(6)" in mysql_ddl
    old = MySQLAccountStore(
        service_name="test", params={"user": "ov", "password": "", "database": "ov"}
    )
    assert old._url.drivername == "mysql+aiomysql"
    assert "INSERT IGNORE" in str(old._default_account_insert().compile(dialect=mysql.dialect()))
    assert "ON CONFLICT" in str(
        store._default_account_insert().compile(dialect=postgresql.dialect())
    )


@pytest.mark.parametrize(
    "params",
    [
        {"user": "ov"},
        {"user": "ov", "database": "ov", "maxsize": 0},
        {"user": "ov", "database": "ov", "port": 70000},
        {"user": "ov", "database": "ov", "connect_timeout": "bad"},
        {"user": "ov", "database": "ov", "sslmode": "bad"},
        {"user": "ov", "database": "ov", "mysql": {}},
        {"user": "ov", "database": "ov", "credential_storage": "encrypted"},
    ],
)
def test_postgres_invalid_parameters(params):
    with pytest.raises(InvalidArgumentError):
        PostgresAccountStore(service_name="test", params=params)


def test_postgres_provider_does_not_import_mysql_driver_or_redis():
    script = """
import sys
class Reject:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"aiomysql", "pymysql", "redis",
                        "openviking.server.account_stores.mysql"}:
            raise AssertionError("unexpected import: " + fullname)
sys.meta_path.insert(0, Reject())
from openviking.server.store_assembly import build_api_key_manager
manager = build_api_key_manager("root", None, service_name="test",
    account_store_provider="postgres",
    account_store_params={"user": "ov", "database": "ov"})
assert manager._store._url.drivername == "postgresql+psycopg"
"""
    subprocess.run([sys.executable, "-c", script], check=True, env=os.environ.copy())


@pytest.fixture(scope="module")
def pg_database():
    raw = os.environ.get("OV_TEST_POSTGRES")
    if not raw:
        pytest.skip("Set OV_TEST_POSTGRES for PostgreSQL account-store integration")
    psycopg = pytest.importorskip("psycopg")
    sql = psycopg.sql
    values = json.loads(raw)
    schema, role = "ov_account_" + uuid4().hex, "ov_runtime_" + uuid4().hex
    password = uuid4().hex
    connection_params = {**values}
    connection_params["dbname"] = connection_params.pop("database")
    admin = psycopg.connect(**connection_params, autocommit=True)
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        script = (
            Path(__file__).parents[2] / "openviking/server/account_stores/sql/postgres/init.sql"
        ).read_text()
        admin.execute(script, prepare=False)
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        admin.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        admin.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        admin.execute(
            sql.SQL("ALTER ROLE {} IN DATABASE {} SET search_path TO {}").format(
                sql.Identifier(role), sql.Identifier(values["database"]), sql.Identifier(schema)
            )
        )
        runtime = {**values, "user": role, "password": password}
        yield runtime, admin, schema, role
    finally:
        admin.execute("ROLLBACK")
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        admin.close()


@pytest.fixture
async def pg_manager(pg_database):
    params, _, _, _ = pg_database
    manager = build_api_key_manager(
        "root",
        None,
        service_name="test-" + uuid4().hex,
        account_store_provider="postgres",
        account_store_params=params,
    )
    await manager.load()
    try:
        yield manager
    finally:
        await manager.close()


async def test_pg_low_privilege_startup_reload_and_concurrent_default(pg_manager, pg_database):
    params, admin, schema, role = pg_database
    assert not admin.execute(
        "SELECT has_schema_privilege(%s, %s, 'CREATE')", (role, schema)
    ).fetchone()[0]
    assert (
        admin.execute(
            "SELECT tableowner FROM pg_tables WHERE schemaname=%s AND tablename='ov_accounts'",
            (schema,),
        ).fetchone()[0]
        != role
    )
    peer = build_api_key_manager(
        "root",
        None,
        service_name=pg_manager._store._service_name,
        account_store_provider="postgres",
        account_store_params=params,
    )
    await pg_manager.close()
    try:
        await asyncio.gather(pg_manager.load(), peer.load(), pg_manager.load())
        assert [a["account_id"] for a in await peer.list_accounts()] == ["default"]
        await pg_manager.create_account("acme", "admin")
        await pg_manager.close()
        await pg_manager.load()
        assert (await pg_manager.get_account("acme"))["user_count"] == 1
    finally:
        await peer.close()


@pytest.mark.parametrize("mode", ["hash", "encrypted"])
async def test_pg_credentials_groups_rotation_and_service_isolation(pg_database, mode):
    params, admin, _, _ = pg_database
    params = {
        **params,
        "credential_storage": mode,
        "credential_encryption_key": base64.b64encode(b"x" * 32).decode(),
    }
    service = "test-" + uuid4().hex

    def manager(name):
        return build_api_key_manager(
            "root",
            None,
            service_name=name,
            account_store_provider="postgres",
            account_store_params=params,
        )

    writer, peer, other = manager(service), manager(service), manager(service + "-other")
    try:
        await asyncio.gather(writer.load(), peer.load(), other.load())
        admin_key = await writer.create_account("acme", "admin")
        user_key = await writer.register_user("acme", "alice")
        await other.create_account("acme", "admin")
        with pytest.raises(UnauthenticatedError):
            await other.resolve_identity(user_key)
        assert (await peer.resolve_identity(admin_key)).role == Role.ADMIN
        await writer.create_group("acme", "engineering")
        assert await writer.add_group_members("acme", "engineering", ["alice", "alice"])
        assert await writer.add_group_members("acme", "engineering", ["alice"])
        assert await peer.get_group_members("acme", "engineering") == ["alice"]
        assert (await peer.resolve_identity(user_key)).group_ids == ("engineering",)
        await writer.set_role("acme", "alice", "admin")
        assert (await peer.resolve_identity(user_key)).role == Role.ADMIN
        page = await peer.list_users_page("acme", name_filter="ali*", limit=1)
        assert page["total"] == 1 and page["account_total"] == 2
        assert page["manager_count"] == 2 and page["key_count"] == 2
        assert ("api_key" in page["users"][0]) == (mode == "encrypted")
        materials = admin.execute(
            "SELECT material FROM ov_accounts_credentials WHERE service_name=%s", (service,)
        ).fetchall()
        assert all(
            user_key not in material and admin_key not in material for (material,) in materials
        )
        new_key = await writer.regenerate_key("acme", "alice", expected_api_key=user_key)
        with pytest.raises(UnauthenticatedError):
            await peer.resolve_identity(user_key)
        assert (await peer.resolve_identity(new_key)).user_id == "alice"
        with pytest.raises(UnauthenticatedError):
            await writer.regenerate_key("acme", "alice", expected_api_key=user_key)
        assert await writer.remove_group_members("acme", "engineering", ["alice"])
        assert (await peer.resolve_identity(new_key)).group_ids == ()
        await writer.delete_group("acme", "engineering")
        assert await peer.get_groups("acme") == []
        await writer._store.revoke_active_api_keys("acme", "alice")
        with pytest.raises(UnauthenticatedError):
            await peer.resolve_identity(new_key)
    finally:
        await asyncio.gather(writer.close(), peer.close(), other.close())


async def test_pg_credential_history_is_bounded_and_revocation_time_is_utc(pg_manager, pg_database):
    _, admin, _, _ = pg_database
    old_key = await pg_manager.create_account("acme", "admin")
    for _ in range(12):
        key = await pg_manager.regenerate_key("acme", "admin")
    with pytest.raises(UnauthenticatedError):
        await pg_manager.resolve_identity(old_key)
    assert (await pg_manager.resolve_identity(key)).user_id == "admin"
    rows = admin.execute(
        "SELECT revoked, revoked_at, active_user_pk FROM ov_accounts_credentials "
        "WHERE service_name=%s",
        (pg_manager._store._service_name,),
    ).fetchall()
    assert len(rows) == 11 and sum(revoked for revoked, _, _ in rows) == 10
    assert all(
        at is not None and at.tzinfo is not None and active is None
        for revoked, at, active in rows
        if revoked
    )


async def test_pg_last_admin_and_deletion_fences_cas_and_cascades(pg_manager, pg_database):
    _, admin, _, _ = pg_database
    await pg_manager.create_account("acme", "admin")
    user_key = await pg_manager.register_user("acme", "alice")
    with pytest.raises(FailedPreconditionError, match="last"):
        await pg_manager.set_role("acme", "admin", "user")
    owner = {"owner_account_id": "acme", "owner_user_id": "admin"}
    with pytest.raises(FailedPreconditionError, match="last"):
        await pg_manager.begin_deletion("acme", "admin", task_id="admin-task", **owner)
    await pg_manager.create_group("acme", "team")
    await pg_manager.add_group_members("acme", "team", ["alice"])
    record, created = await pg_manager.begin_deletion("acme", "alice", task_id="task1", **owner)
    assert created and record["task_id"] == "task1"
    assert not (await pg_manager.begin_deletion("acme", "alice", task_id="ignored", **owner))[1]
    with pytest.raises(UnauthenticatedError):
        await pg_manager.resolve_identity(user_key)
    with pytest.raises(FailedPreconditionError):
        await pg_manager.set_role("acme", "alice", "admin")
    replaced = await pg_manager.replace_deletion_task(
        "acme",
        "alice",
        expected_task_id="task1",
        task_id="task2",
        **owner,
    )
    assert replaced["task_id"] == "task2"
    assert not await pg_manager.finish_deletion("acme", "alice", "task1")
    assert await pg_manager.finish_deletion("acme", "alice", "task2")
    assert await pg_manager.get_group_members("acme", "team") == []
    await pg_manager.begin_deletion("acme", None, task_id="account-task", **owner)
    with pytest.raises(FailedPreconditionError):
        await pg_manager.register_user("acme", "bob")
    assert (await pg_manager.iter_deletions())[0][2]["task_id"] == "account-task"
    assert await pg_manager.finish_deletion("acme", None, "account-task")
    service = pg_manager._store._service_name
    for name in (
        "ov_accounts_users",
        "ov_accounts_groups",
        "ov_accounts_credentials",
        "ov_accounts_group_members",
    ):
        assert (
            admin.execute(
                f"SELECT count(*) FROM {name} WHERE service_name=%s", (service,)
            ).fetchone()[0]
            == 0
        )


async def test_pg_concurrent_trusted_bootstrap_and_duplicate_write_rollback(pg_manager):
    stores = [pg_manager._store]
    peer = PostgresAccountStore(
        service_name=stores[0]._service_name,
        params=pg_manager._store._url.translate_connect_args(username="user"),
    )
    # Connection options include the runtime role's configured search_path.
    await peer.load()
    try:
        results = await asyncio.gather(
            stores[0].ensure_trusted_identities({"trusted": {"alice", "bob"}}),
            peer.ensure_trusted_identities({"trusted": {"alice", "bob"}}),
        )
        assert sum(r["created_accounts"] for r in results) == 1
        assert sum(r["created_users"] for r in results) == 2
        await pg_manager.create_account("acme", "admin")
        with pytest.raises(AlreadyExistsError):
            await pg_manager.create_account("acme", "other")
        assert (await pg_manager.get_account("acme"))["user_count"] == 1
        with pytest.raises(NotFoundError):
            await pg_manager.register_user("missing", "alice")
        assert await peer.get_user("acme", "other") is None
    finally:
        await peer.close()


async def test_pg_cancelled_write_rolls_back_before_pool_reuse(pg_manager):
    store = pg_manager._store
    started = threading.Event()

    async def operation():
        async with store._transaction() as session:
            session.add(Account(service_name=store._service_name, account_id="cancelled"))
            await session.flush()
            started.set()
            await session.execute(text("SELECT pg_sleep(30)"))

    task = asyncio.create_task(store._sql_runtime.run(operation))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await store.get_account("cancelled") is None
    await pg_manager.create_account("survived", "admin")
    assert await store.get_account("survived") is not None


def test_pg_runtime_supports_separate_caller_event_loops(pg_database):
    params, _, _, _ = pg_database
    store = PostgresAccountStore(service_name="test-" + uuid4().hex, params=params)
    try:
        asyncio.run(store.load())
        asyncio.run(store.create_account("acme", "admin"))
        assert asyncio.run(store.get_account("acme"))["user_count"] == 1
    finally:
        asyncio.run(store.close())
    asyncio.run(store.load())
    asyncio.run(store.close())


async def test_pg_missing_schema_fails_without_ddl_or_runtime_lease(pg_database):
    params, _, _, _ = pg_database
    store = PostgresAccountStore(service_name="test-" + uuid4().hex, params=params)
    store._engine_options["connect_args"]["options"] += " -c search_path=pg_catalog"
    with pytest.raises(RuntimeError, match="schema is missing"):
        await store.load()
    assert store._engine is None and not store._sql_lease
    await store.close()
