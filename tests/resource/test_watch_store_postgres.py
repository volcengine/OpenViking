# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL configuration, indexing and lifecycle regression coverage."""

import asyncio
import threading
from contextlib import suppress
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from openviking.resource.watch_store_postgres import PostgresWatchStore
from openviking.resource.watch_store_sql import db_now, watch_tables
from openviking.resource.watch_types import WatchAuthority, WatchPatch, WatchRunResult
from openviking_cli.utils.config.storage_config import StorageConfig, WatchStoreConfig
from tests.resource.test_watch_store import (
    ACCOUNT,
    ENCRYPTION_KEY,
    OWNER,
    create,
)
from tests.resource.test_watch_store import (
    make_store as _make_store,
)

make_store = _make_store

PARAMS = {"user": "u", "database": "d", "credential_encryption_key": ENCRYPTION_KEY}


def test_configuration_builds_postgres_watch_store(tmp_path):
    config = StorageConfig(
        workspace=str(tmp_path), watch_store={"provider": " PostgreS ", "params": PARAMS}
    )
    store = config.build_watch_store(service_name="deployment", viking_fs=None)
    assert isinstance(store, PostgresWatchStore)
    assert store.provider == "postgres"
    assert store._service_name == "deployment"
    assert store._url.drivername == "postgresql+psycopg"
    assert store._url.port == 5432
    assert store._engine_options["isolation_level"] == "READ COMMITTED"
    assert WatchStoreConfig().provider == "file"
    with pytest.raises(ValueError, match="storage.watch_store.provider"):
        WatchStoreConfig(provider="unsupported")


def test_watch_and_legacy_mysql_stores_share_one_sql_runtime():
    from openviking.server.account_stores.mysql import MySQLAccountStore
    from openviking.service.task_store_mysql import MySQLTaskWorkStore
    from openviking.storage.mysql_session import MySQLAsyncSession
    from openviking.storage.sql_session import SQLAsyncSession

    watch = PostgresWatchStore(service_name="deployment", params=PARAMS)
    connection = {"user": "u", "password": "p", "database": "d"}
    tasks = MySQLTaskWorkStore(service_name="deployment", params=connection)
    accounts = MySQLAccountStore(service_name="deployment", params=connection)
    assert watch._sql_runtime is tasks._sql_runtime is accounts._sql_runtime
    assert MySQLAsyncSession is SQLAsyncSession


@pytest.mark.parametrize(
    "override",
    [
        {"unknown": True},
        {"user": ""},
        {"database": ""},
        {"maxsize": 0},
        {"port": 65536},
        {"connect_timeout": 0},
        {"port": "oops"},
        {"sslmode": "invalid"},
        {"table": "bad; DROP TABLE watches"},
        {"table": "x" * 64},
    ],
)
def test_postgres_watch_rejects_invalid_parameters(override):
    with pytest.raises(ValueError):
        PostgresWatchStore(service_name="deployment", params={**PARAMS, **override})


def test_connection_preserves_password_and_tls_parameters():
    password = "p@ss:/?#%"
    store = PostgresWatchStore(
        service_name="deployment",
        params={
            **PARAMS,
            "password": password,
            "maxsize": 2,
            "sslmode": "verify-full",
            "sslrootcert": "/certs/ca.pem",
            "sslcert": "/certs/client.pem",
            "sslkey": "/certs/client.key",
        },
    )
    assert store._url.password == password
    assert store._engine_options["pool_size"] == 2
    assert store._engine_options["max_overflow"] == 0
    assert store._engine_options["connect_args"] == {
        "connect_timeout": 10,
        "sslmode": "verify-full",
        "sslrootcert": "/certs/ca.pem",
        "sslcert": "/certs/client.pem",
        "sslkey": "/certs/client.key",
    }
    clock = str(select(db_now()).compile(dialect=postgresql.dialect()))
    assert "statement_timestamp()" in clock
    assert "DOUBLE PRECISION" in clock


@pytest.mark.asyncio
async def test_initialization_failure_releases_runtime_and_can_retry(monkeypatch):
    import sqlalchemy.ext.asyncio as async_sql

    store = PostgresWatchStore(service_name="deployment", params=PARAMS)
    original = async_sql.create_async_engine

    def fail(*args, **kwargs):
        raise RuntimeError("creation failed")

    monkeypatch.setattr(async_sql, "create_async_engine", fail)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="creation failed"):
            await store.initialize()
        assert store._engine is None
        assert store._sessions is None
        assert not store._runtime_lease
    monkeypatch.setattr(async_sql, "create_async_engine", original)
    await store.close()


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_initialization_error(monkeypatch):
    import sqlalchemy.ext.asyncio as async_sql

    class Engine:
        async def dispose(self):
            raise RuntimeError("dispose failed")

    class Session:
        async def __aenter__(self):
            raise RuntimeError("schema failed")

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(async_sql, "create_async_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(async_sql, "async_sessionmaker", lambda *args, **kwargs: Session)
    store = PostgresWatchStore(service_name="deployment", params=PARAMS)
    with pytest.raises(RuntimeError, match="schema failed"):
        await store.initialize()
    assert store._engine is None
    assert store._sessions is None
    assert not store._runtime_lease
    await store.close()


@pytest.mark.asyncio
async def test_postgres_long_unicode_paths_and_maximum_identity_width(make_store):
    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL B-tree width contract")
    # Four-byte, varied characters avoid PostgreSQL compressing a repeated value.
    wide = "".join(chr(0x10000 + index) for index in range(255))
    store = await make_store("service-" + "s" * 247)
    parent = "viking://resources/" + "".join(chr(0x11000 + index) for index in range(300))
    uri = parent + "/child"
    owner = WatchAuthority(wide, wide, "user")
    task = await create(store, uri, task_id=wide, account_id=wide, user_id=wide, due=True)
    assert (await store.list_tasks_page(owner)).tasks[0]["to_uri"] == uri
    assert [item.task_id for item in await store.deactivate_under_uri(wide, parent)] == [
        task.task_id
    ]
    await store.patch_task(task.task_id, WatchPatch({"is_active": True}), owner)
    run = await store.begin_run(await store.get_candidate(task.task_id))
    assert run is not None
    assert await store.has_running_overlap(wide, [parent])


@pytest.mark.asyncio
async def test_postgres_user_pagination_scans_hash_and_cursor_index_range(make_store):
    from sqlalchemy import event, text

    from openviking.resource.watch_pagination import WatchPageQuery

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL user pagination index contract")
    seed_task = await create(store)
    c = store._tasks.c
    epoch = 1700000000.0
    columns = ", ".join(f'"{name}"' for name in c.keys())
    overrides = {
        "task_id": "g.prefix || lpad(n::text, 6, '0')",
        "account_id": "g.account_id",
        "user_id": "g.user_id",
        "service_name": "g.service_name",
        "deleted": "g.deleted",
        "created_at": ":epoch + (n / 2)",
    }
    values = ", ".join(overrides.get(name, f'w."{name}"') for name in c.keys())

    async def seed():
        async with store._transaction() as session:
            await session.execute(
                text(
                    f'INSERT INTO "{store._table_name}" ({columns}) '
                    f'SELECT {values} FROM "{store._table_name}" AS w '
                    "CROSS JOIN generate_series(0, 999) AS n "
                    "CROSS JOIN (VALUES "
                    "('page-', :account, :owner, :service, false), "
                    "('z-user-', :account, 'other-user', :service, false), "
                    "('z-account-', 'other-account', :owner, :service, false), "
                    "('z-service-', :account, :owner, 'other-service', false), "
                    "('z-deleted-', :account, :owner, :service, true)"
                    ") AS g(prefix, account_id, user_id, service_name, deleted) "
                    "WHERE w.task_id = :task_id AND w.service_name = :service"
                ),
                {
                    "epoch": epoch,
                    "account": OWNER.account_id,
                    "owner": OWNER.user_id,
                    "service": store._service_name,
                    "task_id": seed_task.task_id,
                },
            )
            await session.execute(
                text(
                    f'DELETE FROM "{store._table_name}" '
                    "WHERE task_id = :task_id AND service_name = :service"
                ),
                {"task_id": seed_task.task_id, "service": store._service_name},
            )
            await session.execute(text(f'ANALYZE "{store._table_name}"'))

    await store._sql_runtime.run(seed)
    query = WatchPageQuery(
        OWNER,
        active_only=False,
        limit=10,
        cursor=None,
        store_scope=f"{store.provider}:{store._service_name}:{store._table_name}",
    )
    cursor = query.next_cursor((epoch + 10, "page-000021"))
    statements = []

    def capture(connection, db_cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(store._engine.sync_engine, "before_cursor_execute", capture)
    try:
        page = await store.list_tasks_page(OWNER, limit=10, cursor=cursor)
    finally:
        event.remove(store._engine.sync_engine, "before_cursor_execute", capture)
    assert page.total == 1000
    assert [task["task_id"] for task in page.tasks] == [
        f"page-{index:06d}" for index in range(20, 10, -1)
    ]
    assert page.next_cursor
    following = await store.list_tasks_page(OWNER, limit=10, cursor=page.next_cursor)
    assert following.total == 1000
    assert [task["task_id"] for task in following.tasks] == [
        f"page-{index:06d}" for index in range(10, 0, -1)
    ]
    final = await store.list_tasks_page(OWNER, limit=10, cursor=following.next_cursor)
    assert [task["task_id"] for task in final.tasks] == ["page-000000"]
    assert final.next_cursor is None
    assert (await store.list_tasks_page(WatchAuthority("", "", "root"))).total == 3000
    assert len(statements) == 2
    for statement, _ in statements:
        # Full IDs must remain authorization filters alongside hash scan keys.
        assert ".account_id =" in statement
        assert ".user_id =" in statement
        assert "md5(" in statement

    async def explain():
        async with store._transaction() as session:
            connection = await session.connection()
            statement, parameters = statements[-1]
            result = await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
            )
            return result.scalar_one()[0]["Plan"]

    plan = await store._sql_runtime.run(explain)

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    scans = [
        node for node in nodes(plan) if node.get("Index Name") == f"ix_{store._table_name}_list"
    ]
    assert scans, plan
    condition = scans[0].get("Index Cond", "")
    assert "md5((account_id)" in condition, plan
    assert "md5((user_id)" in condition, plan
    assert "ROW(created_at, (task_id)::text)" in condition, plan
    assert " < ROW(" in condition, plan
    assert scans[0].get("Rows Removed by Filter", 0) == 0, plan
    assert scans[0]["Actual Rows"] == 11, plan
    assert not any(node["Node Type"] == "Sort" for node in nodes(plan)), plan


@pytest.mark.asyncio
async def test_postgres_operations_work_across_event_loops(make_store):
    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL pool loop-affinity contract")
    task = await create(store, due=True)
    state = await asyncio.to_thread(lambda: asyncio.run(store.get_state(task.task_id)))
    assert state.task.task_id == task.task_id
    assert (await store.list_tasks_page(OWNER)).total == 1
    assert state.task.account_id == ACCOUNT


@pytest.mark.asyncio
async def test_postgres_short_prefix_uses_expression_index(make_store):
    from sqlalchemy import text

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL path expression-index contract")
    await create(store, "viking://resources/index/child")

    async def explain():
        async with store._transaction() as session:
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            c = store._tasks.c
            query = select(c.task_id).where(
                store._scope,
                c.account_id == ACCOUNT,
                c.deleted.is_(False),
                store._target_descendants("viking://resources/index"),
            )
            sql = str(
                query.compile(dialect=store._engine.dialect, compile_kwargs={"literal_binds": True})
            )
            result = await session.execute(text("EXPLAIN " + sql))
            return "\n".join(row[0] for row in result)

    plan = await store._sql_runtime.run(explain)
    assert f"ix_{store._table_name}_target_prefix" in plan


@pytest.mark.asyncio
async def test_postgres_due_scan_uses_time_range_index_condition(make_store):
    from sqlalchemy import event, text

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL due scan index contract")
    future = await create(store, created_at=datetime.now() + timedelta(days=1))
    due = await create(store, "viking://resources/due", due=True)
    c = store._tasks.c
    columns = ", ".join(f'"{name}"' for name in c.keys())
    values = ", ".join(
        "'future-' || n::text" if name == "task_id" else f'w."{name}"' for name in c.keys()
    )

    async def seed():
        async with store._transaction() as session:
            await session.execute(
                text(
                    f'INSERT INTO "{store._table_name}" ({columns}) '
                    f'SELECT {values} FROM "{store._table_name}" AS w '
                    "CROSS JOIN generate_series(1, 5000) AS n "
                    "WHERE w.task_id = :task_id AND w.service_name = :service_name"
                ),
                {"task_id": future.task_id, "service_name": store._service_name},
            )
            await session.execute(text(f'ANALYZE "{store._table_name}"'))

    await store._sql_runtime.run(seed)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "next_run_at <=" in statement:
            statements.append((statement, parameters))

    event.listen(store._engine.sync_engine, "before_cursor_execute", capture)
    try:
        candidates = await store.due_candidates(1)
    finally:
        event.remove(store._engine.sync_engine, "before_cursor_execute", capture)
    assert [candidate.task_id for candidate in candidates] == [due.task_id]
    assert len(statements) == 1

    async def explain():
        async with store._transaction() as session:
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            connection = await session.connection()
            statement, parameters = statements[0]
            result = await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
            )
            return result.scalar_one()[0]["Plan"]

    plan = await store._sql_runtime.run(explain)

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    scans = [
        node for node in nodes(plan) if node.get("Index Name") == f"ix_{store._table_name}_due"
    ]
    assert scans, plan
    assert "next_run_at <=" in scans[0].get("Index Cond", ""), plan
    assert scans[0].get("Rows Removed by Filter", 0) == 0, plan


@pytest.mark.asyncio
async def test_postgres_running_rows_share_one_statement_clock(make_store):
    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL statement clock contract")
    for index in range(3):
        task = await create(store, f"viking://resources/running/{index}", due=True)
        assert await store.begin_run(await store.get_candidate(task.task_id)) is not None
    running = await store.running()
    assert len(running) == 3
    assert len({row.now for row in running}) == 1


@pytest.mark.asyncio
async def test_postgres_settlement_uses_one_clock_for_all_timestamps(make_store):
    from sqlalchemy import text

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL settlement clock contract")
    task = await create(store, due=True)
    run = await store.begin_run(await store.get_candidate(task.task_id))
    assert run is not None
    assert await store.attach_task(task.task_id, "execution", started_at=run.started_at)
    assert await store.finish_run(task.task_id, "execution", WatchRunResult(status="completed"))

    async def timestamps():
        async with store._transaction() as session:
            result = await session.execute(
                text(
                    f"SELECT last_execution_time, updated_at, next_run_at, watch_interval "
                    f'FROM "{store._table_name}" WHERE service_name = :service_name '
                    "AND task_id = :task_id"
                ),
                {"service_name": store._service_name, "task_id": task.task_id},
            )
            return result.one()

    last, updated, next_run, interval = await store._sql_runtime.run(timestamps)
    assert last == updated
    assert next_run == last + interval * 60


@pytest.mark.asyncio
async def test_postgres_requires_schema_and_recovers_after_initialize_failure(make_store):
    from sqlalchemy.exc import ProgrammingError

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL schema initialization contract")
    await store.close()
    original = store._tasks
    store._tasks = watch_tables(store._table_name + "_missing", dialect="postgresql")
    try:
        with pytest.raises(ProgrammingError):
            await store.initialize()
        assert store._engine is None
        assert store._sessions is None
        assert not store._runtime_lease
    finally:
        store._tasks = original
    await store.initialize()
    task = await create(store)
    assert (await store.get_task(task.task_id)).task_id == task.task_id


@pytest.mark.asyncio
async def test_postgres_cancellation_rolls_back_before_reusing_pool(make_store):
    from sqlalchemy import text, update

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL cancellation cleanup contract")
    store = await make_store(maxsize=1)
    task = await create(store)
    started = threading.Event()

    async def blocking():
        async with store._transaction() as session:
            await session.execute(
                update(store._tasks)
                .where(store._scope, store._tasks.c.task_id == task.task_id)
                .values(last_error="must roll back")
            )
            started.set()
            await session.execute(text("SELECT pg_sleep(30)"))

    operation = asyncio.create_task(store._sql_runtime.run(blocking))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, timeout=5)
        assert (await store.get_task(task.task_id)).last_error is None
        assert store._engine.pool.checkedout() == 0
    finally:
        if not operation.done():
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(operation, timeout=5)
