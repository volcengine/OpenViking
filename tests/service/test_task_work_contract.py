# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Observable Task/Work lifecycle and persistence contracts."""

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import Mock, create_autospec
from uuid import uuid4

import pytest

from openviking.service.task_domain import TaskPatch, TaskRetentionPolicy, TaskStatus, WorkState
from openviking.service.task_store import PersistentTaskStore, WorkRegistration
from openviking.service.task_tracker import TaskTracker
from openviking_cli.exceptions import FailedPreconditionError
from tests.test_task_tracker import _FakeAgfs

OWNER: dict[str, Any] = {"account_id": "account", "user_id": "user"}


def _relational_backend(provider):
    sqlalchemy = pytest.importorskip("sqlalchemy.ext.asyncio")
    if provider == "postgres":
        pytest.importorskip("psycopg")
        from openviking.service.task_store_postgres import PostgresTaskWorkStore

        store_class = PostgresTaskWorkStore
        schema_names = ["task_store_postgres_init.sql"]
    else:
        pytest.importorskip("aiomysql")
        from openviking.service.task_store_mysql import MySQLTaskWorkStore

        store_class = MySQLTaskWorkStore
        schema_names = ["task_store_init.sql", "changes/0001_task_store_service_name.sql"]
    raw = os.environ.get(f"OV_TEST_{provider.upper()}")
    if not raw:
        pytest.skip(f"Set OV_TEST_{provider.upper()} to exercise the shared Task/Work contract")
    params = {**json.loads(raw), "table": f"ov_contract_{uuid4().hex[:12]}"}
    return sqlalchemy, store_class, params, schema_names


async def _provision_task_schema(admin, params, schema_names):
    sql_dir = Path(__file__).resolve().parents[2] / "openviking/service/sql"
    async with admin.begin() as connection:
        for name in schema_names:
            sql = "\n".join(
                line
                for line in (sql_dir / name).read_text().splitlines()
                if not line.lstrip().startswith("--")
            )
            for statement in sql.replace("ov_tasks", params["table"]).split(";"):
                # The fixture already owns a transaction.
                if statement.strip() and statement.strip().upper() not in {"BEGIN", "COMMIT"}:
                    await connection.exec_driver_sql(statement)


@pytest.fixture(params=["file", "mysql", "postgres"])
async def task_stores(request):
    if request.param == "file":
        store = PersistentTaskStore(_FakeAgfs())
        yield store, store
        return
    sqlalchemy, store_class, params, schema_names = _relational_backend(request.param)
    first = store_class(params=params)
    second = store_class(params=params)
    admin = sqlalchemy.create_async_engine(first._url, **first._engine_options)
    try:
        await _provision_task_schema(admin, params, schema_names)
        await first.load()
        await second.load()
        yield first, second
    finally:
        await first.close()
        await second.close()
        async with admin.begin() as connection:
            await connection.run_sync(first._models.Base.metadata.drop_all)
        await admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["mysql", "postgres"])
async def test_relational_load_requires_schema_and_can_retry(provider):
    sqlalchemy, store_class, params, schema_names = _relational_backend(provider)
    store = store_class(params=params)
    admin = sqlalchemy.create_async_engine(store._url, **store._engine_options)
    try:
        with pytest.raises(RuntimeError, match="schema is missing or incompatible"):
            await store.load()
        assert store._engine is None
        assert not store._sql_lease
        await _provision_task_schema(admin, params, schema_names)
        await asyncio.gather(*[store.load() for _ in range(4)])
        assert store._sql_lease
        await store.close()
        await store.load()
        assert await store.get_task("absent", **OWNER) is None
    finally:
        await store.close()
        async with admin.begin() as connection:
            await connection.run_sync(store._models.Base.metadata.drop_all)
        await admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("release", ["finalize", "withdraw", "replace"])
async def test_duplicate_business_key_rechecks_after_conflict(task_stores, monkeypatch, release):
    from sqlalchemy.exc import IntegrityError

    from openviking.service.task_store_sql import SQLTaskWorkStore

    first, second = task_stores
    if not isinstance(first, SQLTaskWorkStore):
        pytest.skip("Duplicate-key recovery is a relational store contract")
    writer, observer = TaskTracker(first), TaskTracker(second)
    uri = "viking://resources/duplicate-key"
    await observer.create("content_write", uri, task_id="blocker", reject_if_active=True, **OWNER)
    original = first._transaction
    conflicts = 0

    @asynccontextmanager
    async def release_after_rollback():
        nonlocal conflicts
        try:
            async with original() as session:
                yield session
        except IntegrityError:
            # Commit on the other client after the real INSERT failed and
            # rolled back, but before create() starts duplicate recovery.
            conflicts += 1
            if release == "withdraw":
                assert (await second.withdraw_unaccepted("blocker", **OWNER)).is_applied
            else:
                await observer.complete("blocker", {"done": True}, **OWNER)
                if release == "replace":
                    await observer.create(
                        "content_write", uri, task_id="replacement", reject_if_active=True, **OWNER
                    )
            raise

    monkeypatch.setattr(first, "_transaction", release_after_rollback)
    created = await writer.create_if_no_running("content_write", uri, task_id="new", **OWNER)
    assert conflicts == 1
    if release == "replace":
        assert created is None
        assert await observer.get("new", **OWNER) is None
        assert (await observer.get("replacement", **OWNER)).status is TaskStatus.PENDING
    else:
        assert created is not None and created.task_id == "new"
        history = (await observer.get_with_events("new", **OWNER)).execution_events
        assert [item["seq"] for item in history["items"]] == [1]
        assert await observer.create_if_no_running("content_write", uri, **OWNER) is None


@pytest.mark.asyncio
async def test_duplicate_task_id_deleted_before_recovery_is_retried(task_stores, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from openviking.service.task_domain import TaskRecord
    from openviking.service.task_store_sql import SQLTaskWorkStore

    first, second = task_stores
    if not isinstance(first, SQLTaskWorkStore):
        pytest.skip("Duplicate-key recovery is a relational store contract")
    record = TaskRecord(task_id="same-id", task_type="content_write", **OWNER)
    load = first._load_task
    transaction = first._transaction
    injected = False
    conflicts = 0

    async def insert_after_missing_read(*args, **kwargs):
        nonlocal injected
        row = await load(*args, **kwargs)
        if row is None and not injected:
            injected = True
            assert (await second.create(record)).created
        return row

    @asynccontextmanager
    async def delete_after_rollback():
        nonlocal conflicts
        try:
            async with transaction() as session:
                yield session
        except IntegrityError:
            conflicts += 1
            assert (await second.withdraw_unaccepted(record.task_id, **OWNER)).is_applied
            raise

    monkeypatch.setattr(first, "_load_task", insert_after_missing_read)
    monkeypatch.setattr(first, "_transaction", delete_after_rollback)
    assert (await first.create(record)).created
    assert conflicts == 1
    history = (await second.get_task_with_events(record.task_id, **OWNER)).execution_events
    assert [item["seq"] for item in history["items"]] == [1]


@pytest.mark.asyncio
async def test_duplicate_recovery_churn_has_bounded_retries(task_stores, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from openviking.service.task_domain import TaskRecord
    from openviking.service.task_store_sql import SQLTaskWorkStore

    first, second = task_stores
    if not isinstance(first, SQLTaskWorkStore):
        pytest.skip("Duplicate-key recovery is a relational store contract")
    observer = TaskTracker(second)
    record = TaskRecord(
        task_id="new", task_type="content_write", resource_id="viking://resources/churn", **OWNER
    )
    original = first._transaction
    transactions = 0
    conflicts = 0

    @asynccontextmanager
    async def churn():
        nonlocal transactions, conflicts
        transactions += 1
        blocker = f"blocker-{transactions}"
        if transactions % 2:
            await observer.create(
                record.task_type,
                record.resource_id,
                task_id=blocker,
                reject_if_active=True,
                **OWNER,
            )
        try:
            async with original() as session:
                yield session
        except IntegrityError:
            conflicts += 1
            await observer.complete(blocker, {"done": True}, **OWNER)
            raise

    monkeypatch.setattr(first, "_transaction", churn)
    with pytest.raises(IntegrityError):
        await first.create(record, reject_if_active=True)
    assert conflicts == 3
    assert transactions == 6
    assert await second.get_task(record.task_id, **OWNER) is None
    monkeypatch.setattr(first, "_transaction", original)
    assert (await first.create(record, reject_if_active=True)).created


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True], ids=["complete", "cancel"])
async def test_outcome_resource_conflict_preserves_task_and_work(task_stores, cancelled):
    from sqlalchemy import select

    from openviking.service.task_store import TaskAlreadyActive
    from openviking.service.task_store_sql import SQLTaskWorkStore

    first, second = task_stores
    if not isinstance(first, SQLTaskWorkStore):
        pytest.skip("Resource guard conflicts are a relational store contract")
    writer, observer = TaskTracker(first), TaskTracker(second)
    source, target = "viking://resources/source", "viking://resources/target"
    await writer.create("content_write", source, task_id="source", reject_if_active=True, **OWNER)
    await observer.create("content_write", target, task_id="target", reject_if_active=True, **OWNER)
    await writer.start("source", **OWNER)
    await writer.register_work("source", "running", "Semantic", **OWNER)
    await writer.start_work("source", "running", **OWNER)
    await writer.register_work("source", "pending", "Embedding", **OWNER)
    before = await observer.get_with_events("source", **OWNER)

    async def persisted_task():
        Task = second._models.Task
        async with second._session_factory() as session:
            rows = await session.execute(
                select(*Task.__table__.columns).where(
                    Task.service_name == second._service_name, Task.task_id == "source"
                )
            )
            return dict(rows.mappings().one())

    persisted_before = await second._sql_runtime.run(persisted_task)
    outcome = {
        "result": {"done": True},
        "error": "failed retarget" if cancelled else None,
        "resource_id": target,
        "cancelled": cancelled,
        **OWNER,
    }
    with pytest.raises(TaskAlreadyActive) as raised:
        await first.record_outcome("source", **outcome)
    assert raised.value.resource_id == target
    after = await observer.get_with_events("source", **OWNER)
    assert await second._sql_runtime.run(persisted_task) == persisted_before
    # Active processing_seconds is computed at read time and keeps increasing.
    assert after.processing_seconds >= before.processing_seconds
    before_view, after_view = (
        before.to_dict(include_events=True),
        after.to_dict(include_events=True),
    )
    before_view.pop("processing_seconds")
    after_view.pop("processing_seconds")
    assert after_view == before_view
    assert after.auth == before.auth
    assert await observer.get_work_state("source", "running", **OWNER) is WorkState.RUNNING
    assert await observer.get_work_state("source", "pending", **OWNER) is WorkState.PENDING

    # Public complete() must propagate the conflict as well.
    if not cancelled:
        with pytest.raises(TaskAlreadyActive):
            await writer.complete("source", {"done": True}, resource_id=target, **OWNER)
    await observer.complete("target", {"done": True}, **OWNER)
    assert (await first.record_outcome("source", **outcome)).is_applied
    changed = await observer.get("source", **OWNER)
    assert changed.resource_id == target
    assert changed.result == outcome["result"]
    assert changed.status is (TaskStatus.CANCELLING if cancelled else TaskStatus.RUNNING)
    assert await observer.create_if_no_running("content_write", source, **OWNER) is not None


@pytest.mark.asyncio
async def test_terminal_outcome_does_not_acquire_destination_guard(task_stores):
    from openviking.service.task_store_sql import SQLTaskWorkStore

    first, second = task_stores
    if not isinstance(first, SQLTaskWorkStore):
        pytest.skip("Resource guard conflicts are a relational store contract")
    writer, observer = TaskTracker(first), TaskTracker(second)
    target = "viking://resources/target"
    await writer.create("content_write", "source", task_id="source", reject_if_active=True, **OWNER)
    await observer.create("content_write", target, task_id="target", reject_if_active=True, **OWNER)
    await writer.complete("source", {"done": True}, resource_id=target, **OWNER)
    terminal = await observer.get_with_events("source", **OWNER)
    assert terminal.status is TaskStatus.COMPLETED
    assert terminal.resource_id == target
    assert terminal.execution_events["items"][-1]["status"] == "completed"
    assert await writer.create_if_no_running("content_write", target, **OWNER) is None


@pytest.mark.asyncio
async def test_outcome_conflict_retries_if_destination_guard_is_released(task_stores, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from openviking.service.task_store_sql import SQLTaskWorkStore

    first, second = task_stores
    if not isinstance(first, SQLTaskWorkStore):
        pytest.skip("Resource guard conflicts are a relational store contract")
    writer, observer = TaskTracker(first), TaskTracker(second)
    target = "viking://resources/target"
    await writer.create("content_write", "source", task_id="source", reject_if_active=True, **OWNER)
    await observer.create("content_write", target, task_id="target", reject_if_active=True, **OWNER)
    await writer.register_work("source", "work", "Semantic", **OWNER)
    original = first._transaction
    conflicts = 0

    @asynccontextmanager
    async def finalize_after_rollback():
        nonlocal conflicts
        try:
            async with original() as session:
                yield session
        except IntegrityError:
            conflicts += 1
            await observer.complete("target", {"done": True}, **OWNER)
            raise

    monkeypatch.setattr(first, "_transaction", finalize_after_rollback)
    await writer.complete("source", {"done": True}, resource_id=target, **OWNER)
    assert conflicts == 1
    task = await observer.get_with_events("source", **OWNER)
    assert task.resource_id == target
    assert task.result == {"done": True}
    assert [item["seq"] for item in task.execution_events["items"]] == [1]
    await writer.mark_work_done("source", "work", **OWNER)
    assert (await observer.get("source", **OWNER)).status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_work_error_waits_for_task_lock_before_touching_work(task_stores, monkeypatch):
    from openviking.service.task_store import StoreOperationCode
    from openviking.service.task_store_sql import SQLTaskWorkStore

    first, second = task_stores
    if not isinstance(first, SQLTaskWorkStore):
        pytest.skip("Task-before-Work row locking is a relational store contract")
    writer = TaskTracker(first)
    await writer.create("content_write", task_id="task", **OWNER)
    await writer.register_work("task", "work", "Semantic", **OWNER)
    locked, release, attempted = threading.Event(), threading.Event(), threading.Event()
    lock_task = first._lock_task

    async def observe_task_lock(*args, **kwargs):
        attempted.set()
        return await lock_task(*args, **kwargs)

    async def hold_task():
        async with second._transaction() as session:
            assert await second._lock_task(session, "task", **OWNER)
            locked.set()
            assert await asyncio.to_thread(release.wait, 5)

    monkeypatch.setattr(first, "_lock_task", observe_task_lock)
    holder = asyncio.create_task(second._sql_runtime.run(hold_task))
    error_writer = None
    try:
        assert await asyncio.to_thread(locked.wait, 5)
        error_writer = asyncio.create_task(
            first.record_work_error("task", "work", "first error", **OWNER)
        )
        assert await asyncio.to_thread(attempted.wait, 5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(error_writer), 0.1)
        work = await second.get_work("task", "work", **OWNER)
        assert work.error is None
        release.set()
        await asyncio.wait_for(holder, 5)
        assert (await asyncio.wait_for(error_writer, 5)).is_applied
        assert (await second.record_work_error("task", "work", "second error", **OWNER)).accepted
        assert (await second.get_work("task", "work", **OWNER)).error == "first error"
        foreign = await first.record_work_error(
            "task", "work", "foreign error", account_id="other", user_id=OWNER["user_id"]
        )
        assert not foreign.is_applied and foreign.code is StoreOperationCode.WORK_NOT_OPEN
        missing = await first.record_work_error("missing", "work", "error", **OWNER)
        assert not missing.is_applied and missing.code is StoreOperationCode.WORK_NOT_OPEN
    finally:
        release.set()
        await asyncio.gather(
            holder, *([error_writer] if error_writer is not None else []), return_exceptions=True
        )


@pytest.mark.asyncio
async def test_concurrent_events_preserve_history_and_lifecycle(task_stores):
    first, second = task_stores
    writer, observer = TaskTracker(first), TaskTracker(second)
    await writer.create("content_write", task_id="task", **OWNER)
    await writer.start("task", stage="parsing", **OWNER)
    initial = await observer.get_with_events("task", **OWNER)
    assert initial is not None and initial.execution_events is not None
    before = initial.execution_events
    await asyncio.gather(
        *[
            tracker.record_event(
                "task", "waiting_for_descendants", operation=f"work-{index}", **OWNER
            )
            for index, tracker in enumerate([writer, observer] * 6)
        ]
    )
    await writer.start("task", stage="parsing", **OWNER)
    await observer.patch_task("task", TaskPatch(stage="embedding"), **OWNER)
    await writer.fail("task", "provider rejected sk-testsecret123", **OWNER)
    task = await observer.get_with_events("task", **OWNER)
    assert task is not None and task.execution_events is not None
    history = task.execution_events
    items = history["items"]
    assert items[: len(before["items"])] == before["items"]
    assert [item["seq"] for item in items] == list(range(1, 19))
    assert {item["operation"] for item in items[3:15]} == {f"work-{index}" for index in range(12)}
    assert [item["kind"] for item in items[15:]] == [
        "stage_changed",
        "error_recorded",
        "status_changed",
    ]
    assert items[-1]["stage"] == "embedding"
    assert items[-1]["status"] == "failed"
    assert "[REDACTED]" in items[-2]["error"]
    assert "testsecret123" not in json.dumps(history)
    assert history["dropped_count"] == 0
    assert history["started_mid_task"] is False
    assert task.updated_at > task.created_at


@pytest.mark.asyncio
@pytest.mark.parametrize("stage_prefix", ["stage", "阶段" * 64])
async def test_shared_event_history_retains_latest_metadata_after_truncation(
    task_stores, stage_prefix
):
    from openviking.service.task_events import MAX_TASK_EVENT_BYTES, MAX_TASK_EVENTS

    first, second = task_stores
    writer, observer = TaskTracker(first), TaskTracker(second)
    await writer.create("add_resource", task_id="task", **OWNER)
    for index in range(80):
        await writer.patch_task("task", TaskPatch(stage=f"{index}:{stage_prefix}"), **OWNER)
    await writer.cancel("task", **OWNER)
    task = await observer.get_with_events("task", **OWNER)
    assert task is not None and task.execution_events is not None
    history = task.execution_events
    items = history["items"]
    assert len(items) <= MAX_TASK_EVENTS
    assert len(json.dumps(history).encode()) <= MAX_TASK_EVENT_BYTES
    assert history["dropped_count"] + len(items) == 83
    assert [item["seq"] for item in items] == list(range(history["dropped_count"] + 1, 84))
    assert [item["status"] for item in items[-2:]] == ["cancelling", "cancelled"]
    assert history["started_mid_task"] is False


@pytest.mark.asyncio
async def test_store_withdraws_only_tasks_without_registered_work(task_stores):
    first, second = task_stores
    tracker = TaskTracker(first)
    await tracker.create("compile", task_id="unused", **OWNER)
    assert (await second.withdraw_unaccepted("unused", **OWNER)).is_applied
    assert await tracker.get("unused", **OWNER) is None

    await tracker.create("compile", task_id="accepted", **OWNER)
    assert (
        await tracker.register_work("accepted", "work", "ExternalTask", **OWNER)
        is WorkRegistration.CREATED
    )
    assert not (await second.withdraw_unaccepted("accepted", **OWNER)).accepted
    assert await tracker.get("accepted", **OWNER) is not None
    assert await tracker.get_work_state("accepted", "work", **OWNER) is WorkState.PENDING


@pytest.mark.asyncio
async def test_withdrawal_is_atomic_with_work_registration(task_stores):
    first, second = task_stores
    tracker = TaskTracker(first)
    await tracker.create("compile", task_id="task", **OWNER)

    withdrawn, registered = await asyncio.gather(
        second.withdraw_unaccepted("task", **OWNER),
        tracker.register_work("task", "work", "ExternalTask", **OWNER),
    )
    if withdrawn.is_applied:
        assert registered is WorkRegistration.REJECTED
        assert await tracker.get("task", **OWNER) is None
    else:
        assert not withdrawn.accepted
        assert registered is WorkRegistration.CREATED
        assert await tracker.get_work_state("task", "work", **OWNER) is WorkState.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("postcheck", ["query-error", "refused"])
async def test_creator_postcheck_preserves_or_cancels_accepted_task(monkeypatch, postcheck):
    store = PersistentTaskStore(_FakeAgfs())
    creator, replay = TaskTracker(store), TaskTracker(store)
    inserted, release = asyncio.Event(), asyncio.Event()
    create = store.create
    checks = 0
    query_error = ConnectionError("Identity storage unavailable")

    async def guard(account_id, user_id):
        nonlocal checks
        checks += 1
        if checks == 2:
            if postcheck == "query-error":
                raise query_error
            return True
        return False

    async def delayed_create(task, **kwargs):
        result = await create(task, **kwargs)
        inserted.set()
        await release.wait()
        return result

    creator.set_deletion_guard(guard)
    monkeypatch.setattr(store, "create", delayed_create)
    request = asyncio.create_task(creator.create("compile", task_id="task", **OWNER))
    try:
        await asyncio.wait_for(inserted.wait(), 1)
        await replay.create("compile", task_id="task", **OWNER)
        assert (
            await replay.register_work("task", "work", "ExternalTask", **OWNER)
            is WorkRegistration.CREATED
        )
        release.set()
        with pytest.raises(
            ConnectionError if postcheck == "query-error" else FailedPreconditionError
        ) as raised:
            await asyncio.wait_for(request, 1)
        task = await replay.get("task", **OWNER)
        assert task is not None
        if postcheck == "query-error":
            assert raised.value is query_error
            assert task.status is TaskStatus.PENDING
            assert await replay.get_work_state("task", "work", **OWNER) is WorkState.PENDING
            await replay.cancel("task", **OWNER)
        else:
            assert task.status is TaskStatus.CANCELLED
            assert await replay.get_work_state("task", "work", **OWNER) is WorkState.CANCELLED

        await replay.mark_work_done("task", "work", **OWNER)
        assert (await replay.wait("task", timeout=0, **OWNER)).status is TaskStatus.CANCELLED
    finally:
        release.set()
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "root_queue,running,child_running",
    [
        ("MemoryCompile", False, False),
        ("MemoryCompile", True, False),
        ("Inline", False, False),
        ("Inline", True, False),
        ("Inline", True, True),
    ],
)
async def test_cancellation_closes_pending_work_and_waits_for_running_work(
    task_stores, root_queue, running, child_running
):
    first, second = task_stores
    tracker, observer = TaskTracker(first), TaskTracker(second)
    await tracker.create("compile", task_id="task", **OWNER)
    await tracker.register_work("task", "root", root_queue, **OWNER)
    await tracker.register_work("task", "child", "Embedding", **OWNER)
    if running:
        assert (await tracker.start_work("task", "root", **OWNER)).is_applied
        assert (await observer.start_work("task", "root", **OWNER)).is_applied
        assert (await observer.get("task", **OWNER)).status is TaskStatus.RUNNING
    if child_running:
        assert (await tracker.start_work("task", "child", **OWNER)).is_applied
    await tracker.cancel("task", **OWNER)
    assert await observer.get_work_state("task", "child", **OWNER) is (
        WorkState.RUNNING if child_running else WorkState.CANCELLED
    )
    assert not (await observer.start_work("task", "child", **OWNER)).is_applied
    waits_for_running = running and root_queue != "Inline" or child_running
    assert (await observer.get("task", **OWNER)).status is (
        TaskStatus.CANCELLING if waits_for_running else TaskStatus.CANCELLED
    )
    assert await observer.get_work_state("task", "root", **OWNER) is (
        WorkState.RUNNING if running and root_queue != "Inline" else WorkState.CANCELLED
    )
    await observer.cancel("task", **OWNER)
    await tracker.mark_work_done("task", "root", **OWNER)
    if child_running:
        assert (await observer.get("task", **OWNER)).status is TaskStatus.CANCELLING
        await tracker.mark_work_done("task", "child", **OWNER)
    assert not await observer.has_work("task", **OWNER)
    assert (await observer.wait("task", timeout=0, **OWNER)).status is TaskStatus.CANCELLED
    assert await observer.get_work_state("task", "root", **OWNER) is WorkState.CANCELLED
    cancelled = await observer.get("task", **OWNER)
    await tracker.complete("task", {"late": True}, **OWNER)
    await tracker.fail("task", "late failure", **OWNER)
    await tracker.patch_task("task", TaskPatch(result={"late": True}), **OWNER)
    terminal = await observer.get("task", **OWNER)
    assert terminal.status is TaskStatus.CANCELLED
    assert terminal.result == cancelled.result
    assert terminal.error == cancelled.error


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_method", ["cancel", "record_cancelled"])
async def test_remote_inline_execution_observes_terminal_cancellation(
    task_stores, monkeypatch, cancel_method
):
    first, second = task_stores
    tracker, remote = TaskTracker(first), TaskTracker(second)
    await tracker.create("compile", task_id="task", **OWNER)
    started = asyncio.Event()
    work_ids = []

    async def execute():
        work_ids.append(await remote.register_running_task("task", **OWNER))
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await remote.unregister_running_task("task")

    worker = asyncio.create_task(execute())
    polling = None
    try:
        await asyncio.wait_for(started.wait(), 1)
        await getattr(tracker, cancel_method)("task", **OWNER)
        assert (await tracker.get("task", **OWNER)).status is TaskStatus.CANCELLED
        assert await tracker.get_work_state("task", work_ids[0], **OWNER) is WorkState.CANCELLED
        assert not worker.done()
        monkeypatch.setattr("openviking.service.task_tracker._CANCEL_POLL_INTERVAL", 0)
        polling = asyncio.create_task(remote._cancel_poll_loop())
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 1)
        assert remote._work_index.active_tasks_by_owner() == {}
        assert (await tracker.get("task", **OWNER)).status is TaskStatus.CANCELLED
    finally:
        worker.cancel()
        if polling is not None:
            polling.cancel()
        await asyncio.gather(
            worker, *([polling] if polling is not None else []), return_exceptions=True
        )


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
        tracker.wait_for_task_work("task", exclude_work_id="root", **OWNER)
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
@pytest.mark.parametrize("provider", ["mysql", "postgres"])
async def test_relational_requeue_and_concurrent_completion_are_durable(deletion, provider):
    sqlalchemy, store_class, params, schema_names = _relational_backend(provider)
    first = store_class(params=params)
    second = store_class(params=params)
    isolated_store = store_class(
        params=params,
        service_name="contract-isolated",
    )
    admin = sqlalchemy.create_async_engine(first._url, **first._engine_options)
    writer, observer = TaskTracker(first), TaskTracker(second)
    isolated = TaskTracker(isolated_store)
    try:
        await _provision_task_schema(admin, params, schema_names)
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

        assert (await second.withdraw_unaccepted(task_id, **OWNER)).is_applied
        assert await observer.get(task_id, **OWNER) is None
        assert await isolated.get(task_id, **OWNER) is not None
        assert await isolated.get_work_state(task_id, "semantic", **OWNER) is WorkState.PENDING
        assert (
            await writer.create_if_no_running("content_write", uri, task_id=task_id, **OWNER)
            is not None
        )

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
            from sqlalchemy import func, select

            for model in (first._models.Task, first._models.Work, first._models.Event):
                remaining = await connection.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.service_name == first._service_name, model.task_id == task_id)
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
