# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL connection validation and relational transaction regressions."""

import asyncio
from unittest.mock import Mock

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from openviking.service.task_domain import TaskRecord, TaskStatus
from openviking.service.task_store_postgres import PostgresTaskWorkStore
from openviking.service.task_store_sql import SQLTaskWorkStore
from openviking_cli.utils.config.storage_config import StorageConfig
from tests.service.test_task_work_contract import OWNER
from tests.service.test_task_work_contract import task_stores as _task_stores

task_stores = _task_stores


def test_postgres_factory_and_tls_settings():
    params = {
        "user": "openviking",
        "database": "tasks",
        "service_name": "params-service",
        "sslmode": "verify-full",
        "sslrootcert": "/ca.pem",
    }
    tracker = StorageConfig(
        task_store={"provider": " POSTGRES ", "params": params}
    ).build_task_tracker(None, service_name="production")
    store = tracker._store
    assert isinstance(store, PostgresTaskWorkStore)
    assert store._service_name == "production"
    assert params["service_name"] == "params-service"
    assert store._url.drivername == "postgresql+psycopg"
    assert store._url.password is None
    assert store._url.port == 5432
    assert store._engine_options["connect_args"]["sslrootcert"] == "/ca.pem"
    assert store._engine_options["isolation_level"] == "READ COMMITTED"


@pytest.mark.parametrize(
    "table",
    [
        pytest.param("a" * 38, id="too-long"),
        pytest.param("task-store", id="invalid-characters"),
    ],
)
def test_postgres_rejects_invalid_configuration(table):
    with pytest.raises(ValueError, match=r"task_store\.table .* at most 37 characters"):
        PostgresTaskWorkStore(params={"user": "u", "database": "db", "table": table})


def test_postgres_schema_and_unique_violation_detection():
    store = PostgresTaskWorkStore(params={"user": "u", "database": "db", "table": "a" * 37})
    for table in store._models.Base.metadata.tables.values():
        sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "utf8mb4" not in sql and "ascii_bin" not in sql and "InnoDB" not in sql
        assert 'COLLATE "C"' in sql
    for code in ("23505", "23502", "23514", None):
        error = IntegrityError("insert", {}, Mock(sqlstate=code))
        assert store._is_duplicate_error(error) == (code == "23505")


@pytest.mark.asyncio
async def test_concurrent_task_id_is_idempotent_and_case_sensitive(task_stores):
    first, second = task_stores
    record = TaskRecord(task_id="Task", task_type="content_write", **OWNER)
    results = await asyncio.gather(*[store.create(record) for store in [first, second] * 6])
    assert sum(result.created for result in results) == 1
    assert all(result.task.task_id == "Task" for result in results)
    lower = await first.create(TaskRecord(task_id="task", task_type="content_write", **OWNER))
    assert lower.created
    assert (await second.get_task("Task", **OWNER)).status is TaskStatus.PENDING
    if isinstance(first, SQLTaskWorkStore):
        with pytest.raises(ValueError, match="another owner"):
            await first.create(
                TaskRecord(
                    task_id="Task", task_type="content_write", account_id="other", user_id="user"
                )
            )
    assert (await second.get_task("task", **OWNER)).task_id == "task"


@pytest.mark.asyncio
@pytest.mark.parametrize("task_stores", ["mysql", "postgres", "sqlite"], indirect=True)
async def test_cancelled_creation_rolls_back_task_and_events(task_stores, monkeypatch):
    first, second = task_stores
    original = first._append_event_history

    async def cancel_after_events(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError

    monkeypatch.setattr(first, "_append_event_history", cancel_after_events)
    with pytest.raises(asyncio.CancelledError):
        await first.create(TaskRecord(task_id="cancelled", task_type="content_write", **OWNER))
    assert await second.get_task("cancelled", **OWNER) is None

    async def read_events():
        event = second._models.Event
        async with second._session_factory() as session:
            rows = await session.execute(
                select(event.seq).where(
                    event.service_name == second._service_name, event.task_id == "cancelled"
                )
            )
            return rows.scalars().all()

    assert await second._sql_runtime.run(read_events) == []
    monkeypatch.setattr(first, "_append_event_history", original)
    result = await first.create(TaskRecord(task_id="cancelled", task_type="content_write", **OWNER))
    assert result.created
    history = (await second.get_task_with_events("cancelled", **OWNER)).execution_events
    assert [(event["seq"], event["kind"]) for event in history["items"]] == [(1, "created")]


@pytest.mark.asyncio
@pytest.mark.parametrize("task_stores", ["postgres"], indirect=True)
async def test_non_unique_integrity_error_is_not_an_active_task_conflict(task_stores):
    first, second = task_stores

    async def add_constraint():
        async with first._transaction() as session:
            await session.execute(
                text(
                    f'ALTER TABLE "{first._models.Task.__tablename__}" '
                    "ADD CONSTRAINT test_task_type CHECK (task_type <> 'invalid')"
                )
            )

    await first._sql_runtime.run(add_constraint)
    with pytest.raises(IntegrityError) as raised:
        await first.create(
            TaskRecord(task_id="invalid", task_type="invalid", **OWNER), reject_if_active=True
        )
    assert raised.value.orig.sqlstate == "23514"
    assert await second.get_task("invalid", **OWNER) is None
    assert (
        await first.create(TaskRecord(task_id="valid", task_type="content_write", **OWNER))
    ).created


@pytest.mark.asyncio
@pytest.mark.parametrize("task_stores", ["postgres"], indirect=True)
async def test_outcome_other_unique_constraint_is_not_a_business_conflict(task_stores):
    from openviking.service.task_tracker import TaskTracker

    first, second = task_stores
    writer, observer = TaskTracker(first), TaskTracker(second)
    await writer.create("content_write", "source", task_id="source", **OWNER)
    await observer.create("content_write", "target", task_id="target", **OWNER)
    await writer.register_work("source", "work", "Semantic", **OWNER)

    async def add_constraint():
        async with first._transaction() as session:
            await session.execute(
                text(
                    f'ALTER TABLE "{first._models.Task.__tablename__}" '
                    "ADD CONSTRAINT test_resource UNIQUE (resource_id_hash)"
                )
            )

    await first._sql_runtime.run(add_constraint)
    with pytest.raises(IntegrityError) as raised:
        await writer.complete("source", {"done": True}, resource_id="target", **OWNER)
    assert raised.value.orig.diag.constraint_name == "test_resource"
    restored = await observer.get("source", **OWNER)
    assert restored.resource_id == "source"
    assert restored.result is None


@pytest.mark.asyncio
async def test_postgres_scalar_status_requires_load():
    store = PostgresTaskWorkStore(params={"user": "unused", "database": "unused"})
    with pytest.raises(RuntimeError, match=r"PostgresTaskWorkStore.load\(\) must be called"):
        await store.get_task_status("missing", **OWNER)


@pytest.mark.asyncio
async def test_postgres_scalar_status_live_owner_service_and_none(task_stores):
    from openviking.service.task_tracker import TaskTracker

    first, second = task_stores
    if not isinstance(first, PostgresTaskWorkStore):
        pytest.skip("PostgreSQL scalar status contract")
    writer = TaskTracker(first)
    other = PostgresTaskWorkStore(
        params=first._url.translate_connect_args(username="user")
        | {"table": first._models.Task.__tablename__},
        service_name="other-scalar_status",
    )
    # Use the original engine parameters, including fixture credentials and TLS.
    other._url = first._url
    other._engine_options = dict(first._engine_options)
    await other.load()
    try:
        await writer.create(
            "content_write", "scalar_status-source", task_id="scalar_status", **OWNER
        )
        for owner in (
            OWNER,
            {"account_id": None, "user_id": None},
            {"account_id": OWNER["account_id"], "user_id": None},
            {"account_id": None, "user_id": OWNER["user_id"]},
        ):
            assert await second.get_task_status("scalar_status", **owner) is TaskStatus.PENDING
        for owner in (
            {"account_id": "foreign", "user_id": OWNER["user_id"]},
            {"account_id": OWNER["account_id"], "user_id": "foreign"},
        ):
            assert await second.get_task_status("scalar_status", **owner) is None
        assert await other.get_task_status("scalar_status", account_id=None, user_id=None) is None
        assert await second.get_task_status("absent", **OWNER) is None
        await writer.start("scalar_status", **OWNER)
        assert await second.get_task_status("scalar_status", **OWNER) is TaskStatus.RUNNING
        await writer.complete("scalar_status", {"done": True}, **OWNER)
        assert await second.get_task_status("scalar_status", **OWNER) is TaskStatus.COMPLETED
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_postgres_scalar_status_pool_transaction_cancel_and_disconnect(
    task_stores, monkeypatch
):
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import AsyncConnection

    from openviking.service.task_tracker import TaskTracker

    first, second = task_stores
    if not isinstance(first, PostgresTaskWorkStore):
        pytest.skip("PostgreSQL scalar status contract")
    # Recreate the reader engine with exactly one reusable connection.
    await second.close()
    second._engine_options.update(pool_size=1, max_overflow=0)
    await second.load()
    observed = []

    def inspect(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT") and ".status" in statement:
            observed.append(conn.connection.dbapi_connection.autocommit)

    event.listen(second._engine.sync_engine, "before_cursor_execute", inspect)
    try:
        writer = TaskTracker(first)
        await writer.create("content_write", "pool16", task_id="pool16", **OWNER)
        assert await second.get_task_status("pool16", **OWNER) is TaskStatus.PENDING
        assert observed == [True]

        async def rollback():
            async with second._transaction() as session:
                connection = await session.connection()
                raw = await connection.get_raw_connection()
                assert raw.driver_connection.autocommit is False
                await session.execute(
                    text(
                        f'UPDATE "{second._models.Task.__tablename__}" SET status = :status '
                        "WHERE task_id = :task_id"
                    ),
                    {"status": TaskStatus.RUNNING.value, "task_id": "pool16"},
                )
                raise RuntimeError("scalar_status rollback")

        with pytest.raises(RuntimeError, match="scalar_status rollback"):
            await second._sql_runtime.run(rollback)
        assert await first.get_task_status("pool16", **OWNER) is TaskStatus.PENDING
        scalar = AsyncConnection.scalar
        entered = __import__("threading").Event()

        async def slow(self, statement, *args, **kwargs):
            entered.set()
            return await scalar(self, text("SELECT pg_sleep(30)"))

        with monkeypatch.context() as patch:
            patch.setattr(AsyncConnection, "scalar", slow)
            pending = asyncio.create_task(second.get_task_status("pool16", **OWNER))
            assert await asyncio.to_thread(entered.wait, 5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        assert (
            await asyncio.wait_for(second.get_task_status("pool16", **OWNER), 5)
            is TaskStatus.PENDING
        )

        async def backend_id():
            async with second._engine.connect() as connection:
                return await connection.scalar(text("SELECT pg_backend_pid()"))

        pid = await second._sql_runtime.run(backend_id)

        async def terminate():
            async with first._engine.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                return await connection.scalar(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                )

        assert await first._sql_runtime.run(terminate)
        assert (
            await asyncio.wait_for(second.get_task_status("pool16", **OWNER), 5)
            is TaskStatus.PENDING
        )
        await TaskTracker(second).complete("pool16", {"done": True}, **OWNER)
        assert await first.get_task_status("pool16", **OWNER) is TaskStatus.COMPLETED
    finally:
        event.remove(second._engine.sync_engine, "before_cursor_execute", inspect)
