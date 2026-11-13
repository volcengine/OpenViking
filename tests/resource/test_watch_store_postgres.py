# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL configuration, indexing and lifecycle regression coverage."""

import asyncio
import threading

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from openviking.resource.watch_store_postgres import PostgresWatchStore
from openviking.resource.watch_store_sql import db_now, watch_tables
from openviking.resource.watch_types import WatchAuthority, WatchPatch
from openviking_cli.utils.config.storage_config import StorageConfig, WatchStoreConfig
from tests.resource.test_watch_store import (
    ACCOUNT,
    ENCRYPTION_KEY,
    OWNER,
    create,
    make_store,  # noqa: F401
)

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
    assert "clock_timestamp()" in clock
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
async def test_postgres_long_unicode_paths_and_maximum_identity_width(make_store):  # noqa: F811
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
async def test_postgres_operations_work_across_event_loops(make_store):  # noqa: F811
    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL pool loop-affinity contract")
    task = await create(store, due=True)
    state = await asyncio.to_thread(lambda: asyncio.run(store.get_state(task.task_id)))
    assert state.task.task_id == task.task_id
    assert (await store.list_tasks_page(OWNER)).total == 1
    assert state.task.account_id == ACCOUNT


@pytest.mark.asyncio
async def test_postgres_short_prefix_uses_expression_index(make_store):  # noqa: F811
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

    plan = await store._mysql_runtime.run(explain)
    assert f"ix_{store._table_name}_target_prefix" in plan


@pytest.mark.asyncio
async def test_postgres_requires_schema_and_recovers_after_initialize_failure(make_store):  # noqa: F811
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
async def test_postgres_cancellation_rolls_back_before_reusing_pool(make_store):  # noqa: F811
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

    operation = asyncio.create_task(store._mysql_runtime.run(blocking))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert (await store.get_task(task.task_id)).last_error is None
        assert store._engine.pool.checkedout() == 0
    finally:
        if not operation.done():
            operation.cancel()
            try:
                await operation
            except asyncio.CancelledError:
                pass
