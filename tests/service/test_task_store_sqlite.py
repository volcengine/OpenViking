# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite persistence, isolation and read/write concurrency contracts."""

import asyncio
import sqlite3
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from openviking.service.task_domain import TaskRecord
from openviking.service.task_store_sqlite import SQLiteTaskWorkStore
from openviking_cli.utils.config.storage_config import StorageConfig
from tests.service.test_task_work_contract import OWNER


@pytest.mark.asyncio
async def test_sqlite_factory_persistence_and_query_plan(tmp_path):
    config = StorageConfig(workspace=str(tmp_path), task_store={"provider": " SQLITE "})
    first = config.build_task_tracker(None, service_name="production")._store
    second = config.build_task_tracker(None, service_name="other")._store
    try:
        await first.load()
        for task_id in ("a", "c", "b"):
            await first.create(
                TaskRecord(task_id=task_id, task_type="compile", created_at=100, **OWNER)
            )
        await first.close()
        await first.load()
        await second.load()
        assert [task.task_id for task in await first.list(limit=2, **OWNER)] == ["c", "b"]
        assert [task.task_id for task in await first.list(limit=2, before=(100, "b"), **OWNER)] == [
            "a"
        ]
        assert await second.list(**OWNER) == []

        async def inspect_database():
            async with first._session_factory() as session:
                assert await session.scalar(text("PRAGMA journal_mode")) == "wal"
                for internal in ("", " AND is_internal = 0"):
                    plan = await session.execute(
                        text(
                            "EXPLAIN QUERY PLAN SELECT * FROM ov_tasks "
                            "WHERE service_name = :service AND account_id = :account "
                            f"AND user_id = :user{internal} "
                            "ORDER BY created_at DESC, task_id DESC LIMIT 50"
                        ),
                        {"service": "production", "account": "account", "user": "user"},
                    )
                    details = " ".join(row[3] for row in plan)
                    assert "USING INDEX" in details
                    assert "TEMP B-TREE" not in details

        await first._sql_runtime.run(inspect_database)
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_sqlite_rejects_newer_schema_and_can_retry(tmp_path):
    path = tmp_path / "tasks.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=2")
    store = SQLiteTaskWorkStore(params={"path": str(path)})
    try:
        with pytest.raises(RuntimeError, match="Unsupported SQLite task schema"):
            await store.load()
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            connection.execute("PRAGMA user_version=0")
        await store.load()
        assert (
            await store.create(TaskRecord(task_id="task", task_type="compile", **OWNER))
        ).created
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_wal_reads_during_write_and_lock_timeout_recovers(tmp_path):
    params = {"path": str(tmp_path / "tasks.db"), "busy_timeout_ms": 50}
    first, second = SQLiteTaskWorkStore(params=params), SQLiteTaskWorkStore(params=params)
    locked, release = threading.Event(), threading.Event()
    holder = None
    try:
        await first.load()
        await second.load()
        await first.create(TaskRecord(task_id="task", task_type="compile", **OWNER))

        async def hold_write():
            async with first._transaction() as session:
                await session.execute(text("UPDATE ov_tasks SET stage='uncommitted'"))
                locked.set()
                await asyncio.to_thread(release.wait)

        holder = asyncio.create_task(first._sql_runtime.run(hold_write))
        assert await asyncio.to_thread(locked.wait, 3)
        # WAL readers see committed data without waiting for this writer.
        assert (await asyncio.wait_for(second.get_task("task", **OWNER), 3)).stage is None
        with pytest.raises(OperationalError, match="locked"):
            await second.create(TaskRecord(task_id="blocked", task_type="compile", **OWNER))
        release.set()
        await holder
        assert (await second.get_task("task", **OWNER)).stage == "uncommitted"
        assert await second.get_task("blocked", **OWNER) is None
        assert (
            await second.create(TaskRecord(task_id="blocked", task_type="compile", **OWNER))
        ).created
    finally:
        release.set()
        if holder is not None:
            await holder
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_sqlite_unrelated_unique_violation_is_not_business_conflict(tmp_path):
    from openviking.service.task_tracker import TaskTracker

    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    tracker = TaskTracker(store)
    try:
        await store.load()
        await tracker.create("content_write", "source", task_id="source", **OWNER)
        await tracker.create("content_write", "target", task_id="target", **OWNER)
        await tracker.register_work("source", "work", "Semantic", **OWNER)

        async def add_constraint():
            async with store._transaction() as session:
                await session.execute(
                    text("CREATE UNIQUE INDEX test_resource ON ov_tasks(resource_id_hash)")
                )

        await store._sql_runtime.run(add_constraint)
        with pytest.raises(IntegrityError):
            await tracker.complete("source", {"done": True}, resource_id="target", **OWNER)
        task = await tracker.get("source", **OWNER)
        assert task.resource_id == "source"
        assert task.result is None
    finally:
        await store.close()
