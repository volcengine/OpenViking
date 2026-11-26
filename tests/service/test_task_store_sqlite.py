# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite persistence, isolation and read/write concurrency contracts."""

import asyncio
import sqlite3
import threading

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import tasks as tasks_router
from openviking.service.task_domain import TaskRecord, TaskStatus
from openviking.service.task_store_sqlite import SQLiteTaskWorkStore
from openviking.service.task_tracker import TaskTracker
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.storage_config import StorageConfig
from tests.service.test_task_work_contract import OWNER


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 50])
@pytest.mark.parametrize("filtered", [False, True])
async def test_sqlite_http_cursor_pages_are_complete_after_restart(
    tmp_path, monkeypatch, limit, filtered
):
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db")})
    tracker = TaskTracker(store)
    monkeypatch.setattr(tasks_router, "get_task_tracker", lambda: tracker)
    app = FastAPI()
    app.include_router(tasks_router.router)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier(OWNER["account_id"], OWNER["user_id"]), role=Role.USER
    )
    records = [
        TaskRecord(
            task_id=f"task-{index:03}",
            task_type="compile" if index % 2 else "fs_rm",
            status=TaskStatus.COMPLETED,
            created_at=100 + index // 70,
            meta={"internal": index % 11 == 0},
            **OWNER,
        )
        for index in range(137)
    ]
    expected = sorted(
        (
            task
            for task in records
            if not task.meta["internal"] and (not filtered or task.task_type == "compile")
        ),
        key=lambda task: (task.created_at, task.task_id),
        reverse=True,
    )
    params = {"pagination": "cursor", "limit": limit}
    if filtered:
        params["task_type"] = "compile"
    try:
        await store.load()
        for task in reversed(records):
            await store.create(task)
        await store.create(
            TaskRecord(
                task_id="foreign-task",
                task_type="compile",
                created_at=1000,
                account_id="other",
                user_id=OWNER["user_id"],
            )
        )
        await store.create(
            TaskRecord(
                task_id="foreign-user",
                task_type="compile",
                created_at=1000,
                account_id=OWNER["account_id"],
                user_id="other",
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            collected = []
            cursors = set()
            for page_index in range(len(expected) + 1):
                response = await client.get("/api/v1/tasks", params=params)
                assert response.status_code == 200
                page = response.json()["result"]
                ids = [task["task_id"] for task in page["items"]]
                start = page_index * limit
                assert ids == [task.task_id for task in expected[start : start + limit]]
                collected.extend(ids)
                assert page["has_more"] == (len(collected) < len(expected))
                if not page["has_more"]:
                    assert page["next_cursor"] is None
                    break
                cursor = page["next_cursor"]
                assert cursor and cursor not in cursors
                cursors.add(cursor)
                # Send the API-issued cursor unchanged, including across a store restart.
                params["cursor"] = cursor
                if page_index == 0:
                    await store.close()
                    await store.load()
            else:
                pytest.fail("Cursor pagination did not terminate")
            assert collected == [task.task_id for task in expected]
            assert len(set(collected)) == len(collected)
            empty = await client.get(
                "/api/v1/tasks",
                params={"pagination": "cursor", "limit": limit, "task_type": "absent"},
            )
            assert empty.status_code == 200
            assert empty.json()["result"] == {"items": [], "has_more": False, "next_cursor": None}
    finally:
        await store.close()


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
