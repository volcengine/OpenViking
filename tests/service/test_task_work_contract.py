# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Observable Task/Work lifecycle and persistence contracts."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import Mock, create_autospec
from uuid import uuid4

import pytest

from openviking.service.task_domain import TaskRetentionPolicy, TaskStatus, WorkState
from openviking.service.task_store import PersistentTaskStore, WorkRegistration
from openviking.service.task_tracker import TaskTracker
from tests.test_task_tracker import _FakeAgfs

OWNER = {"account_id": "account", "user_id": "user"}


@pytest.fixture
async def tracker():
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    await tracker.create("content_write", task_id="task", **OWNER)
    await tracker.start("task", **OWNER)
    return tracker


@pytest.mark.asyncio
async def test_wait_for_work_observes_only_explicit_ids(tracker):
    for work_id in ("first", "unrelated"):
        await tracker.register_work("task", work_id, "Semantic", **OWNER)

    await tracker.mark_work_done("task", "first", **OWNER)
    await tracker.wait_for_work("task", ["first"], timeout=0, **OWNER)

    assert await tracker.has_work("task", **OWNER)


@pytest.mark.asyncio
async def test_task_work_wait_includes_recovered_and_later_enqueued_work(tracker):
    await tracker.register_work("task", "root", "Inline", **OWNER)
    await tracker.reconcile_delivery("task", "semantic", "Semantic", **OWNER)
    waiting = asyncio.create_task(
        tracker.wait_for_task_work("task", exclude_work_id="root", poll_interval=0.001, **OWNER)
    )
    try:
        await asyncio.sleep(0)
        assert not waiting.done()
        await tracker.register_work("task", "embedding", "Embedding", **OWNER)
        await tracker.mark_work_done("task", "semantic", **OWNER)
        with pytest.raises(TimeoutError):
            await tracker.wait_for_task_work("task", exclude_work_id="root", timeout=0, **OWNER)
        await tracker.mark_work_done("task", "embedding", **OWNER)
        await asyncio.wait_for(waiting, timeout=1)
        assert await tracker.has_work("task", **OWNER)
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


def test_mysql_task_store_schema_uses_service_name_partition():
    from openviking.service.task_store_mysql import MySQLTaskWorkStore
    from openviking.service.task_store_mysql_schema import make_task_store_models

    store = MySQLTaskWorkStore(
        params={
            "user": "openviking",
            "password": "change-me",
            "database": "openviking",
            "service_name": "tenant-a",
        }
    )
    assert store._service_name == "tenant-a"

    override = MySQLTaskWorkStore(
        params={
            "user": "openviking",
            "password": "change-me",
            "database": "openviking",
            "service_name": "tenant-a",
        },
        service_name="tenant-b",
    )
    assert override._service_name == "tenant-b"

    models = make_task_store_models("ov_contract")
    Task, Work, Event = models.Task, models.Work, models.Event

    assert [column.name for column in Task.__table__.primary_key.columns] == [
        "service_name",
        "task_id",
    ]
    assert [column.name for column in Work.__table__.primary_key.columns] == [
        "service_name",
        "task_id",
        "work_id",
    ]
    assert [column.name for column in Event.__table__.primary_key.columns] == [
        "service_name",
        "task_id",
        "seq",
    ]

    task_uniques = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in Task.__table__.constraints
        if constraint.name
    }
    assert task_uniques["uq_ov_contract_active_business_key"] == [
        "service_name",
        "active_business_key",
    ]
    task_indexes = {
        index.name: [column.name for column in index.columns] for index in Task.__table__.indexes
    }
    assert task_indexes["ix_ov_contract_owner_created"] == [
        "service_name",
        "account_id",
        "user_id",
        "created_at",
    ]
    assert task_indexes["ix_ov_contract_acct_type_created"] == [
        "service_name",
        "account_id",
        "task_type",
        "created_at",
    ]
    assert task_indexes["ix_ov_contract_acct_status_created"] == [
        "service_name",
        "account_id",
        "status",
        "created_at",
    ]
    assert task_indexes["ix_ov_contract_acct_resource_created"] == [
        "service_name",
        "account_id",
        "resource_id_hash",
        "created_at",
    ]
    assert task_indexes["ix_ov_contract_acct_internal_created"] == [
        "service_name",
        "account_id",
        "is_internal",
        "created_at",
    ]
    assert "resource_id_hash" in Task.__table__.columns


@pytest.mark.asyncio
@pytest.mark.parametrize("deletion", ["manual", "expired"])
async def test_mysql_requeue_and_concurrent_completion_are_durable(deletion):
    raw = os.environ.get("OV_TEST_MYSQL")
    if not raw:
        pytest.skip("Set OV_TEST_MYSQL to exercise the shared Task/Work contract")
    sqlalchemy = pytest.importorskip("sqlalchemy.ext.asyncio")
    pytest.importorskip("aiomysql")
    from openviking.service import task_store_mysql

    params = {**json.loads(raw), "table": f"ov_contract_{uuid4().hex[:12]}"}
    first = task_store_mysql.MySQLTaskWorkStore(params=params)
    second = task_store_mysql.MySQLTaskWorkStore(params=params)
    isolated_store = task_store_mysql.MySQLTaskWorkStore(
        params=params,
        service_name="contract-isolated",
    )
    admin = sqlalchemy.create_async_engine(first._url, **first._engine_options)
    sql_dir = Path(task_store_mysql.__file__).parent / "sql"
    schema_files = [
        sql_dir / "task_store_init.sql",
        sql_dir / "changes/0001_task_store_service_name.sql",
    ]
    writer, observer = TaskTracker(first), TaskTracker(second)
    isolated = TaskTracker(isolated_store)
    try:
        async with admin.begin() as connection:
            for schema in schema_files:
                for statement in schema.read_text().replace("ov_tasks", params["table"]).split(";"):
                    if statement.strip():
                        await connection.exec_driver_sql(statement)
        await first.load()
        await second.load()
        await isolated_store.load()
        uri = "viking://resources/" + "/".join(["directory" * 4] * 8)
        candidates = await asyncio.gather(
            writer.create_if_no_running("content_write", uri, **OWNER),
            observer.create_if_no_running("content_write", uri, **OWNER),
        )
        assert sum(task is not None for task in candidates) == 1
        task_id = next(task.task_id for task in candidates if task is not None)
        assert await observer.get(task_id, account_id="other", user_id=OWNER["user_id"]) is None
        assert await observer.get(task_id, account_id=OWNER["account_id"], user_id="other") is None

        assert await isolated.get(task_id, **OWNER) is None
        isolated_task = await isolated.create_if_no_running(
            "content_write", uri, task_id=task_id, **OWNER
        )
        assert isolated_task is not None
        await isolated.register_work(task_id, "semantic", "Semantic", **OWNER)

        await writer.start(task_id, **OWNER)
        await writer.register_work(task_id, "semantic", "Semantic", **OWNER)
        await observer.register_work(task_id, "embedding", "Embedding", **OWNER)
        assert (
            await observer.register_work(task_id, "semantic", "Semantic", **OWNER)
            is WorkRegistration.EXISTING
        )
        await writer.complete(task_id, {"done": True}, **OWNER)
        await writer.start_work(task_id, "semantic", **OWNER)
        await observer.mark_work_requeued(task_id, "semantic", **OWNER)
        assert (await observer.get(task_id, **OWNER)).status is TaskStatus.RUNNING
        await writer.start_work(task_id, "semantic", **OWNER)

        # The last two Work items can finish on different worker event loops.
        await asyncio.gather(
            writer.mark_work_done(task_id, "semantic", **OWNER),
            asyncio.to_thread(
                lambda: asyncio.run(observer.mark_work_done(task_id, "embedding", **OWNER))
            ),
        )
        completed = await observer.wait(task_id, timeout=0, **OWNER)
        assert completed.status is TaskStatus.COMPLETED
        assert completed.resource_id == uri
        assert completed.result["queue_status"]["Semantic"] == {
            "processed": 1,
            "requeue_count": 1,
            "error_count": 0,
            "errors": [],
        }
        assert completed.result["queue_status"]["Embedding"]["processed"] == 1
        await writer.mark_work_failed(task_id, "semantic", "late failure", **OWNER)
        assert (
            await observer.register_work(task_id, "late", "Semantic", **OWNER)
            is WorkRegistration.REJECTED
        )

        await first.close()
        await second.close()
        await second.load()
        restored = await observer.get_with_events(task_id, **OWNER)
        assert restored.status is TaskStatus.COMPLETED
        assert restored.result == completed.result
        assert restored.execution_events["items"][-1]["status"] == "completed"
        assert (
            await observer.build_queue_status(task_id, **OWNER) == completed.result["queue_status"]
        )
        if deletion == "manual":
            assert await observer.delete_tasks(**OWNER) == 1
        else:
            expired = await second.delete_expired(
                TaskRetentionPolicy(completed_seconds=0, failed_seconds=0),
                now=completed.updated_at + 1,
                limit=1,
            )
            assert expired.deleted_count == 1
        assert await observer.get(task_id, **OWNER) is None
        assert await observer.get_work_state(task_id, "semantic", **OWNER) is None
        assert await observer.get_work_state(task_id, "embedding", **OWNER) is None
        assert await isolated.get(task_id, **OWNER) is not None
        assert await isolated.get_work_state(task_id, "semantic", **OWNER) is WorkState.PENDING
        async with admin.connect() as connection:
            for table in first._models.table_names:
                remaining = await connection.exec_driver_sql(
                    f"SELECT COUNT(*) FROM `{table}` WHERE service_name = %s AND task_id = %s",
                    (first._service_name, task_id),
                )
                assert remaining.scalar_one() == 0
    finally:
        await first.close()
        await second.close()
        await isolated_store.close()
        async with admin.begin() as connection:
            await connection.run_sync(first._models.Base.metadata.drop_all)
        await admin.dispose()


@pytest.mark.asyncio
async def test_terminal_work_result_is_monotonic_and_bounded():
    agfs = _FakeAgfs()
    tracker = TaskTracker(PersistentTaskStore(agfs))
    await tracker.create("content_write", task_id="task", **OWNER)
    await tracker.register_work("task", "work", "Embedding", **OWNER)
    await tracker.complete("task", {"done": True}, **OWNER)
    errors = [f"failure {index}" for index in range(150)]

    await tracker.mark_work_failed("task", "work", errors[0], errors=errors, **OWNER)
    await tracker.mark_work_done("task", "work", context_count_delta=3, **OWNER)
    await tracker.mark_work_failed("task", "work", "later failure", **OWNER)

    restored = await TaskTracker(PersistentTaskStore(agfs)).get("task", **OWNER)
    assert restored.status is TaskStatus.FAILED
    assert restored.error == errors[0]
    assert restored.result["queue_status"]["Embedding"] == {
        "processed": 0,
        "requeue_count": 0,
        "error_count": 150,
        "errors": [{"message": error} for error in errors[:100]],
    }
    assert await tracker.get_context_count("task", **OWNER) == 0


@pytest.mark.asyncio
async def test_finalization_write_failure_keeps_work_retryable(monkeypatch):
    agfs = _FakeAgfs()
    tracker = TaskTracker(PersistentTaskStore(agfs))
    await tracker.create("content_write", task_id="task", **OWNER)
    await tracker.start("task", **OWNER)
    await tracker.register_work("task", "work", "Embedding", **OWNER)
    await tracker.complete("task", {"done": True}, **OWNER)
    write = agfs.write
    monkeypatch.setattr(agfs, "write", Mock(side_effect=OSError("write failed")))

    with pytest.raises(OSError, match="write failed"):
        await tracker.mark_work_done("task", "work", vector_written=True, **OWNER)

    assert (await tracker.get("task", **OWNER)).status is TaskStatus.RUNNING
    assert await tracker.get_work_state("task", "work", **OWNER) is WorkState.PENDING
    restored = await TaskTracker(PersistentTaskStore(agfs)).get("task", **OWNER)
    assert restored.status is TaskStatus.RUNNING

    monkeypatch.setattr(agfs, "write", write)
    await tracker.mark_work_done("task", "work", vector_written=True, **OWNER)
    assert (await tracker.wait("task", timeout=0, **OWNER)).status is TaskStatus.COMPLETED
    assert await tracker.get_context_count("task", **OWNER) == 1


@pytest.mark.asyncio
async def test_deleting_task_releases_retained_work_status(monkeypatch):
    agfs = _FakeAgfs()
    tracker = TaskTracker(PersistentTaskStore(agfs))
    foreign = {"account_id": "other", "user_id": "user"}
    for task_id in ("first", "second"):
        await tracker.create("content_write", task_id=task_id, **OWNER)
        await tracker.register_work(task_id, "work", "Semantic", **OWNER)
        await tracker.complete(task_id, {"done": True}, **OWNER)
    await tracker.create("content_write", task_id="foreign", **foreign)
    await tracker.complete("foreign", {"done": True}, **foreign)

    with pytest.raises(RuntimeError, match="Cannot delete active task"):
        await tracker.delete_tasks(**OWNER)
    for task_id in ("first", "second"):
        assert await tracker.get(task_id, **OWNER) is not None
        await tracker.mark_work_done(task_id, "work", **OWNER)

    read = create_autospec(agfs.read, side_effect=agfs.read)
    monkeypatch.setattr(agfs, "read", read)
    assert await tracker.delete_tasks(**OWNER) == 2
    # Cloud storage must read each owned task file only once for deletion.
    assert read.call_count == 2
    assert not await tracker.has_work("first", **OWNER)
    assert (await tracker.build_queue_status("first", **OWNER))["Semantic"]["processed"] == 0
    assert await tracker.get("second", **OWNER) is None
    assert await tracker.get("foreign", **foreign) is not None
