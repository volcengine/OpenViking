# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL configuration, indexing and lifecycle regression coverage."""

import asyncio
import logging
import re
import threading
from contextlib import suppress
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from openviking.resource.watch_store_postgres import PostgresWatchStore
from openviking.resource.watch_store_postgres_schema import (
    POSTGRES_WATCH_INDEX_KEYS,
    postgres_watch_type_name,
)
from openviking.resource.watch_store_sql import db_now, watch_tables
from openviking.resource.watch_types import WatchAuthority, WatchPatch, WatchRunResult
from openviking_cli.utils.config.storage_config import StorageConfig, WatchStoreConfig
from tests.resource.test_watch_store import (
    ACCOUNT,
    ENCRYPTION_KEY,
    OWNER,
    POSTGRES_SCHEMA,
    create,
)
from tests.resource.test_watch_store import (
    make_store as _make_store,
)

make_store = _make_store

PARAMS = {"user": "u", "database": "d", "credential_encryption_key": ENCRYPTION_KEY}


@pytest.mark.parametrize(
    "key",
    [
        "created_att",
        "md5(account_typo::text)",
        "substr(target_typo::text, 1, 128)",
        "unsupported(account_id)",
    ],
)
def test_postgres_metadata_rejects_unknown_columns_and_expressions(monkeypatch, key):
    from openviking.resource import watch_store_sql

    monkeypatch.setattr(
        watch_store_sql,
        "POSTGRES_WATCH_INDEX_KEYS",
        {**POSTGRES_WATCH_INDEX_KEYS, "invalid": ("service_name", key)},
    )
    with pytest.raises(ValueError, match="unknown PostgreSQL Watch index key"):
        watch_tables("ov_watch_tasks", dialect="postgresql")


def _postgres_table_definition(statement):
    """Compare the column contract, ignoring SQL/Python default differences."""
    normalized = " ".join(str(statement).split()).rstrip(";")
    match = re.fullmatch(r"CREATE TABLE (\w+) \((.*)\)", normalized)
    assert match, normalized
    table_name, body = match.groups()
    columns = {}
    primary_key = ()
    # Split only commas outside parentheses (VARCHAR sizes / primary-key lists).
    for entry in re.split(r",\s*(?![^()]*\))", body):
        entry = entry.strip()
        if entry.startswith("PRIMARY KEY"):
            primary_key = tuple(
                name.strip() for name in re.fullmatch(r"PRIMARY KEY \((.*)\)", entry)[1].split(",")
            )
            continue
        name, rest = entry.split(" ", 1)
        sql_type = re.split(r"\s+(?:COLLATE|DEFAULT|NOT NULL|NULL)\b", rest, maxsplit=1)[0]
        collation = re.search(r'COLLATE "([^"]+)"', rest)
        columns[name] = (
            postgres_watch_type_name(sql_type),
            "NOT NULL" not in rest,
            collation[1] if collation else None,
        )
    return table_name, columns, primary_key


@pytest.mark.parametrize("table_name", ["ov_watch_tasks", "custom_watch_tasks"])
def test_postgres_metadata_and_deployment_ddl_share_column_contract(table_name):
    ddl = POSTGRES_SCHEMA.read_text().replace("ov_watch_tasks", table_name)
    ddl = "\n".join(line for line in ddl.splitlines() if not line.lstrip().startswith("--"))
    deployed = next(
        statement for statement in ddl.split(";") if statement.strip().startswith("CREATE TABLE")
    )
    table = watch_tables(table_name, dialect="postgresql")
    metadata = CreateTable(table).compile(dialect=postgresql.dialect())
    assert _postgres_table_definition(metadata) == _postgres_table_definition(deployed)


@pytest.mark.parametrize("table_name", ["ov_watch_tasks", "custom_watch_tasks"])
def test_postgres_metadata_and_deployment_ddl_share_index_definitions(table_name):
    def definition(statement):
        normalized = " ".join(str(statement).split()).rstrip(";")
        match = re.fullmatch(r"CREATE INDEX (\w+) ON (\w+) \((.+)\)", normalized)
        assert match, normalized
        name, table, keys = match.groups()
        # Deployment DDL can omit the implicit varchar-to-text casts.
        return name, (table, "".join(keys.split()).replace("::text", ""))

    ddl = POSTGRES_SCHEMA.read_text().replace("ov_watch_tasks", table_name)
    ddl = "\n".join(line for line in ddl.splitlines() if not line.lstrip().startswith("--"))
    deployed = dict(
        definition(statement)
        for statement in ddl.split(";")
        if statement.strip().startswith("CREATE INDEX")
    )
    table = watch_tables(table_name, dialect="postgresql")
    metadata = dict(
        definition(CreateIndex(index).compile(dialect=postgresql.dialect()))
        for index in table.indexes
    )
    expected = {
        f"ix_{table_name}_{suffix}": (
            table_name,
            "".join(",".join(keys).split()).replace("::text", ""),
        )
        for suffix, keys in POSTGRES_WATCH_INDEX_KEYS.items()
    }
    assert metadata == deployed == expected


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
@pytest.mark.parametrize("prefix_chars", [127, 128, 129, 147, 900])
@pytest.mark.parametrize("character", ["a", "\U00011000"])
async def test_postgres_prefix_boundaries_check_full_uri(make_store, prefix_chars, character):
    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL path expression-index contract")
    base = "viking://resources/"
    parent = base + character * (prefix_chars - len(base) - 1)
    exact = await create(store, parent)
    child = await create(store, parent + "/child")
    sibling = await create(store, parent + "sibling/child")
    # For long paths this sibling has the same entire indexed key.
    shared_prefix = await create(store, parent[:-1] + "b/child")
    other_account = await create(store, parent + "/other", account_id="other-account")
    moved = await store.move(
        account_id=ACCOUNT,
        from_uri=parent + "/",
        to_uri="viking://resources/moved",
    )
    assert set(moved.task_ids) == {exact.task_id, child.task_id}
    assert (await store.get_task(child.task_id)).to_uri == "viking://resources/moved/child"
    for task in (sibling, shared_prefix, other_account):
        assert (await store.get_task(task.task_id)).to_uri == task.to_uri


@pytest.mark.asyncio
async def test_postgres_long_prefix_limits_expression_index_candidates(make_store):
    from sqlalchemy import text

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL long-path index scan contract")
    # A 147-character directory prefix previously scanned every account row.
    parent = "viking://resources/" + "a" * 127
    assert len(parent + "/") == 147
    task = await create(store, parent + "/child")
    c = store._tasks.c
    columns = ", ".join(f'"{name}"' for name in c.keys())
    unrelated_uri = "'viking://resources/unrelated/' || n::text"
    overrides = {
        "task_id": "'unrelated-' || n::text",
        "target_uri": unrelated_uri,
        "target_uri_key": unrelated_uri,
    }
    values = ", ".join(overrides.get(name, f'w."{name}"') for name in c.keys())

    async def explain():
        async with store._transaction() as session:
            await session.execute(
                text(
                    f'INSERT INTO "{store._table_name}" ({columns}) '
                    f'SELECT {values} FROM "{store._table_name}" AS w '
                    "CROSS JOIN generate_series(1, 19999) AS n "
                    "WHERE w.task_id = :task_id AND w.service_name = :service"
                ),
                {"task_id": task.task_id, "service": store._service_name},
            )
            await session.execute(text(f'ANALYZE "{store._table_name}"'))
            query = select(c.task_id).where(
                store._scope,
                c.account_id == ACCOUNT,
                c.deleted.is_(False),
                store._target_descendants(parent),
            )
            compiled = str(
                query.compile(dialect=store._engine.dialect, compile_kwargs={"literal_binds": True})
            )
            result = await session.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + compiled)
            )
            return result.scalar_one()[0]["Plan"]

    plan = await store._sql_runtime.run(explain)

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    scans = [
        node
        for node in nodes(plan)
        if node.get("Index Name") == f"ix_{store._table_name}_target_prefix"
    ]
    assert scans, plan
    condition = scans[0].get("Index Cond", "")
    assert "substr((target_uri_key)::text, 1, 128)" in condition, plan
    assert ">=" in condition and "<" in condition, plan
    assert plan["Actual Rows"] == 1, plan
    assert sum(node.get("Rows Removed by Filter", 0) for node in nodes(plan)) == 0, plan
    assert [item.task_id for item in await store.deactivate_under_uri(ACCOUNT, parent)] == [
        task.task_id
    ]


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
        with pytest.raises(RuntimeError, match="001_watch_tasks_postgres.sql") as error:
            await store.initialize()
        assert isinstance(error.value.__cause__, ProgrammingError)
        assert error.value.__cause__.orig.sqlstate == "42P01"
        assert store._engine is None
        assert store._sessions is None
        assert not store._runtime_lease
    finally:
        store._tasks = original
    await store.initialize()
    task = await create(store)
    assert (await store.get_task(task.task_id)).task_id == task.task_id


@pytest.mark.asyncio
async def test_postgres_missing_column_reports_migration_and_can_retry(make_store):
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL schema initialization contract")

    async def alter_column(action):
        async with store._transaction() as session:
            await session.execute(text(f'ALTER TABLE "{store._table_name}" {action}'))

    await store._sql_runtime.run(lambda: alter_column("DROP COLUMN last_error"))
    await store.close()
    with pytest.raises(RuntimeError, match="missing required columns") as error:
        await store.initialize()
    assert "001_watch_tasks_postgres.sql" in str(error.value)
    assert isinstance(error.value.__cause__, ProgrammingError)
    assert error.value.__cause__.orig.sqlstate == "42703"
    assert store._engine is None
    assert store._sessions is None
    assert not store._runtime_lease

    # Repair externally; failed startup must not migrate.
    import psycopg

    def repair():
        params = store._url.translate_connect_args(username="user", database="dbname")
        with psycopg.connect(**params, **store._engine_options["connect_args"]) as connection:
            connection.execute(f'ALTER TABLE "{store._table_name}" ADD COLUMN last_error TEXT')

    await asyncio.to_thread(repair)
    await store.initialize()
    assert (await store.list_tasks_page(OWNER)).total == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("alteration", "message"),
    [
        ("ALTER COLUMN payload TYPE TEXT USING payload::text", "payload type"),
        ("ALTER COLUMN next_run_at TYPE NUMERIC", "next_run_at type"),
        ("ALTER COLUMN enabled DROP NOT NULL", "enabled nullable"),
        ('DROP CONSTRAINT "{table}_pkey"', "primary key"),
        ('ALTER COLUMN user_id TYPE VARCHAR(255) COLLATE "POSIX"', "user_id collation"),
        ("ALTER COLUMN account_id TYPE VARCHAR(32)", "account_id type"),
    ],
)
async def test_postgres_schema_drift_is_rejected_before_reading_tasks(
    make_store, alteration, message
):
    from sqlalchemy import text

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL column structure initialization contract")
    await create(store)

    async def alter():
        async with store._transaction() as session:
            await session.execute(
                text(
                    f'ALTER TABLE "{store._table_name}" '
                    + alteration.format(table=store._table_name)
                )
            )

    await store._sql_runtime.run(alter)
    await store.close()
    with pytest.raises(RuntimeError, match=message) as error:
        await store.initialize()
    assert "001_watch_tasks_postgres.sql" in str(error.value)
    assert store._engine is None
    assert store._sessions is None
    assert not store._runtime_lease


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "wrong_definition", "partial", "invalid", "renamed"])
async def test_postgres_initialization_checks_usable_index_definitions(
    make_store, caplog, monkeypatch, state
):
    import psycopg
    from sqlalchemy import text

    from openviking.resource.watch_store_postgres import logger

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL schema index initialization contract")
    monkeypatch.setattr(logger, "handlers", [*logger.handlers, caplog.handler])
    # Capture once even when the application enables propagation to root.
    monkeypatch.setattr(logger, "propagate", False)
    caplog.set_level(logging.WARNING, logger=logger.name)
    await store.close()
    await store.initialize()
    assert not [record for record in caplog.records if record.name == logger.name]
    prefix_index = f"ix_{store._table_name}_target_prefix"
    list_index = f"ix_{store._table_name}_list"
    if state == "invalid":
        # An unsuccessful concurrent unique build leaves the correct expression
        # definition present, but indisvalid=false. Startup must detect that state.
        prefix = "viking://resources/" + "p" * 150
        await create(store, prefix + "/a")
        await create(store, prefix + "/b")

    async def alter_indexes():
        async with store._transaction() as session:
            if state == "renamed":
                await session.execute(
                    text(f'ALTER INDEX "{prefix_index}" RENAME TO "{prefix_index}_renamed"')
                )
                return
            await session.execute(text(f'DROP INDEX "{prefix_index}"'))
            if state == "missing":
                await session.execute(text(f'DROP INDEX "{list_index}"'))
            elif state == "wrong_definition":
                await session.execute(
                    text(
                        f'CREATE INDEX "{prefix_index}" ON "{store._table_name}" '
                        "(service_name, account_id, deleted, substr(target_uri_key, 1, 64))"
                    )
                )
            elif state == "partial":
                await session.execute(
                    text(
                        f'CREATE INDEX "{prefix_index}" ON "{store._table_name}" '
                        "(service_name, account_id, deleted, substr(target_uri_key, 1, 128)) "
                        "WHERE enabled"
                    )
                )

    await store._sql_runtime.run(alter_indexes)
    await store.close()
    if state == "invalid":

        def failed_build():
            params = store._url.translate_connect_args(username="user", database="dbname")
            with psycopg.connect(
                **params, **store._engine_options["connect_args"], autocommit=True
            ) as connection:
                with pytest.raises(psycopg.errors.UniqueViolation):
                    connection.execute(
                        f'CREATE UNIQUE INDEX CONCURRENTLY "{prefix_index}" '
                        f'ON "{store._table_name}" '
                        "(service_name, account_id, deleted, substr(target_uri_key, 1, 128))"
                    )
                valid, _ = connection.execute(
                    "SELECT indisvalid, indisready FROM pg_index WHERE indexrelid = %s::regclass",
                    (prefix_index,),
                ).fetchone()
                assert not valid

        await asyncio.to_thread(failed_build)
    caplog.clear()
    await store.initialize()
    warnings = [record.getMessage() for record in caplog.records if record.name == logger.name]
    if state == "renamed":
        assert warnings == []
    else:
        assert len(warnings) == 1
        assert prefix_index in warnings[0]
        assert "001_watch_tasks_postgres.sql" in warnings[0]
        if state == "missing":
            assert list_index in warnings[0]
    assert store._engine is not None
    assert (await store.list_tasks_page(OWNER)).total == (2 if state == "invalid" else 0)


@pytest.mark.asyncio
async def test_postgres_metadata_schema_initializes_without_index_warnings(
    make_store, caplog, monkeypatch
):
    import psycopg

    from openviking.resource.watch_store_postgres import logger

    store = await make_store()
    if store.provider != "postgres":
        pytest.skip("PostgreSQL metadata/DDL index contract")
    await store.close()

    def provision_metadata():
        params = store._url.translate_connect_args(username="user", database="dbname")
        with psycopg.connect(**params, **store._engine_options["connect_args"]) as connection:
            connection.execute(f'DROP TABLE "{store._table_name}"')
            connection.execute(str(CreateTable(store._tasks).compile(dialect=postgresql.dialect())))
            for index in store._tasks.indexes:
                connection.execute(str(CreateIndex(index).compile(dialect=postgresql.dialect())))

    await asyncio.to_thread(provision_metadata)
    monkeypatch.setattr(logger, "handlers", [*logger.handlers, caplog.handler])
    caplog.set_level(logging.WARNING, logger=logger.name)
    caplog.clear()
    await store.initialize()
    assert not [record for record in caplog.records if record.name == logger.name]
    task = await create(store)
    assert (await store.list_tasks_page(OWNER)).tasks[0]["task_id"] == task.task_id


@pytest.mark.asyncio
async def test_postgres_initialization_preserves_non_schema_database_errors(monkeypatch):
    from sqlalchemy.exc import ProgrammingError

    from openviking.resource.watch_store_sql import SQLWatchStore

    class PermissionError(Exception):
        sqlstate = "42501"

    error = ProgrammingError("SELECT", {}, PermissionError("permission denied"))

    async def fail(self, session):
        raise error

    monkeypatch.setattr(SQLWatchStore, "_validate_schema", fail)
    store = PostgresWatchStore(service_name="deployment", params=PARAMS)
    with pytest.raises(ProgrammingError) as caught:
        await store._validate_schema(None)
    assert caught.value is error


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
