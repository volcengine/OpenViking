# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL connection validation and relational transaction regressions."""

import asyncio
from unittest.mock import Mock

import pytest
from sqlalchemy import text
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
    "override",
    [
        {"user": ""},
        {"database": ""},
        {"maxsize": 0},
        {"port": 65536},
        {"connect_timeout": -1},
        {"port": "invalid"},
        {"sslmode": "invalid"},
        {"ssl": True},
        {"table": "a" * 38},
        {"table": "a;DROP"},
    ],
)
def test_postgres_rejects_invalid_configuration(override):
    with pytest.raises(ValueError):
        PostgresTaskWorkStore(params={"user": "u", "database": "db", **override})


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


@pytest.mark.asyncio
async def test_cancelled_creation_rolls_back_task_and_events(task_stores, monkeypatch):
    first, second = task_stores
    if not isinstance(first, SQLTaskWorkStore):
        pytest.skip("Transaction rollback is a relational store contract")
    original = first._append_event_history

    async def cancel_after_events(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError

    monkeypatch.setattr(first, "_append_event_history", cancel_after_events)
    with pytest.raises(asyncio.CancelledError):
        await first.create(TaskRecord(task_id="cancelled", task_type="content_write", **OWNER))
    assert await second.get_task("cancelled", **OWNER) is None
    monkeypatch.setattr(first, "_append_event_history", original)
    result = await first.create(TaskRecord(task_id="cancelled", task_type="content_write", **OWNER))
    assert result.created
    history = (await second.get_task_with_events("cancelled", **OWNER)).execution_events
    assert history["items"][0]["seq"] == 1


@pytest.mark.asyncio
async def test_non_unique_integrity_error_is_not_an_active_task_conflict(task_stores):
    first, second = task_stores
    if not isinstance(first, PostgresTaskWorkStore):
        pytest.skip("PostgreSQL SQLSTATE classification contract")

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
async def test_outcome_other_unique_constraint_is_not_a_business_conflict(task_stores):
    from openviking.service.task_tracker import TaskTracker

    first, second = task_stores
    if not isinstance(first, PostgresTaskWorkStore):
        pytest.skip("PostgreSQL constraint classification contract")
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
