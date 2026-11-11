# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Observable Task/Work lifecycle and persistence contracts."""

import asyncio
import json
import os
import sys
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


async def _waiting_after_read(tracker, monkeypatch, *, interval=60):
    from openviking.service import task_tracker

    monkeypatch.setattr(task_tracker, "_WAIT_INITIAL_POLL_INTERVAL", interval)
    monkeypatch.setattr(task_tracker, "_WAIT_MAX_POLL_INTERVAL", interval)
    queried = asyncio.Event()
    read = tracker.get_task_status

    async def status(*args, **kwargs):
        result = await read(*args, **kwargs)
        queried.set()
        return result

    monkeypatch.setattr(tracker, "get_task_status", status)
    waiter = asyncio.create_task(tracker.wait("task", **OWNER))
    await asyncio.wait_for(queried.wait(), 2)
    return waiter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "complete",
        "foreign-complete",
        "fail",
        "cancel",
        "done",
        "foreign-done",
        "work-failed",
        "discard",
        "settle",
        "requeue",
    ],
)
async def test_local_task_commit_wakes_all_waiters(task_stores, monkeypatch, operation):
    store, _ = task_stores
    writer, reader = TaskTracker(store), TaskTracker(store)
    await writer.create("compile", task_id="task", **OWNER)
    await writer.start("task", **OWNER)
    work_operations = ("done", "foreign-done", "work-failed", "discard", "settle", "requeue")
    if operation in work_operations:
        await writer.register_work("task", "work", "Embedding", **OWNER)
        await writer.start_work("task", "work", **OWNER)
        if operation == "requeue":
            await writer.cancel("task", **OWNER)
        else:
            await writer.complete("task", {"ok": True}, **OWNER)
        assert (await reader.get("task", **OWNER)).status in (
            TaskStatus.RUNNING,
            TaskStatus.CANCELLING,
        )
    waiters = [await _waiting_after_read(reader, monkeypatch) for _ in range(3)]
    try:
        if operation == "complete":
            await writer.complete("task", {"ok": True}, **OWNER)
        elif operation == "foreign-complete":
            await asyncio.to_thread(
                lambda: asyncio.run(writer.complete("task", {"ok": True}, **OWNER))
            )
        elif operation == "fail":
            await writer.fail("task", "failed", **OWNER)
        elif operation == "cancel":
            await writer.cancel("task", **OWNER)
        elif operation == "done":
            await writer.mark_work_done("task", "work", **OWNER)
        elif operation == "foreign-done":
            await asyncio.to_thread(
                lambda: asyncio.run(writer.mark_work_done("task", "work", **OWNER))
            )
        elif operation == "work-failed":
            await writer.mark_work_failed("task", "work", "failed", **OWNER)
        elif operation == "discard":
            await writer.discard_work("task", "work", **OWNER)
        elif operation == "settle":
            await writer.settle_work("task", "work", **OWNER)
        else:
            await writer.mark_work_requeued("task", "work", **OWNER)
        results = await asyncio.wait_for(asyncio.gather(*waiters), 2)
        expected = await reader.get("task", **OWNER)
        assert all(task == expected for task in results)
        assert expected.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        assert store._task_changes.waiter_count == 0
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


@pytest.mark.asyncio
async def test_completion_during_status_read_is_not_lost(task_stores, monkeypatch):
    store, _ = task_stores
    tracker = TaskTracker(store)
    await tracker.create("compile", task_id="task", **OWNER)
    read = tracker.get_task_status
    first_read = True

    async def racing_read(*args, **kwargs):
        nonlocal first_read
        status = await read(*args, **kwargs)
        if first_read:
            first_read = False
            await tracker.complete("task", {"ok": True}, **OWNER)
        return status

    monkeypatch.setattr(tracker, "get_task_status", racing_read)
    from openviking.service import task_tracker

    monkeypatch.setattr(task_tracker, "_WAIT_INITIAL_POLL_INTERVAL", 60)
    result = await asyncio.wait_for(tracker.wait("task", **OWNER), 2)
    assert result.status is TaskStatus.COMPLETED
    assert store._task_changes.waiter_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["withdraw", "delete"])
async def test_removal_wakes_waiters_with_missing_record(task_stores, monkeypatch, operation):
    store, _ = task_stores
    tracker = TaskTracker(store)
    await tracker.create("compile", task_id="task", **OWNER)
    waiter = await _waiting_after_read(tracker, monkeypatch)
    try:
        if operation == "withdraw":
            await store.withdraw_unaccepted("task", **OWNER)
        else:
            await store.delete_tasks("task", allow_active_types=("compile",), **OWNER)
        with pytest.raises(KeyError, match="Task not found"):
            await asyncio.wait_for(waiter, 2)
        assert store._task_changes.waiter_count == 0
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_publication_does_not_notify(task_stores, monkeypatch):
    store, other = task_stores
    tracker = TaskTracker(store)
    await tracker.create("compile", task_id="task", **OWNER)
    error = OSError("write/transaction failed")
    if isinstance(store, PersistentTaskStore):

        async def fail_write(*args):
            raise error

        monkeypatch.setattr(store, "_write_task", fail_write)
    else:
        transaction = store._transaction

        @asynccontextmanager
        async def fail_transaction():
            async with transaction() as session:
                yield session
                raise error

        monkeypatch.setattr(store, "_transaction", fail_transaction)
    with store._subscribe_task("task", **OWNER) as changed:
        with pytest.raises(OSError, match="write/transaction failed"):
            await tracker.complete("task", {}, **OWNER)
        await asyncio.sleep(0)
        assert not changed.done()
        assert (await other.get_task("task", **OWNER)).status is TaskStatus.PENDING


@pytest.mark.asyncio
async def test_non_status_changes_do_not_wake_task_waiters(task_stores):
    store, _ = task_stores
    tracker = TaskTracker(store)
    await tracker.create("compile", task_id="task", **OWNER)
    await tracker.start("task", **OWNER)
    await tracker.register_work("task", "work", "Embedding", **OWNER)
    with store._subscribe_task("task", **OWNER) as changed:
        await tracker.complete("task", {"ok": True}, **OWNER)
        await tracker.patch_task("task", TaskPatch(stage="embedding"), **OWNER)
        await tracker.start_work("task", "work", **OWNER)
        await tracker.register_work("task", "work", "Embedding", **OWNER)
        await asyncio.sleep(0)
        assert not changed.done()
        assert (await tracker.get("task", **OWNER)).status is TaskStatus.RUNNING
        await tracker.mark_work_done("task", "work", **OWNER)
        await asyncio.wait_for(changed, 2)
    assert (await tracker.wait("task", timeout=0, **OWNER)).status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_creation_and_expiry_publish_task_hints(task_stores):
    store, _ = task_stores
    tracker = TaskTracker(store)
    with store._subscribe_task("task", **OWNER) as created:
        await tracker.create("compile", task_id="task", **OWNER)
        await asyncio.wait_for(created, 2)
    await tracker.complete("task", {}, **OWNER)
    task = await tracker.get("task", **OWNER)
    with store._subscribe_task("task", **OWNER) as deleted:
        result = await store.delete_expired(
            TaskRetentionPolicy(completed_seconds=1, failed_seconds=1),
            now=task.updated_at + 2,
            limit=10,
        )
        assert result.deleted_count == 1
        await asyncio.wait_for(deleted, 2)
    assert await tracker.get("task", **OWNER) is None


@pytest.mark.asyncio
async def test_mysql_cancellation_before_commit_rolls_back_without_notification(
    task_stores, monkeypatch
):
    store, other = task_stores
    if isinstance(store, PersistentTaskStore):
        pytest.skip("MySQL transaction boundary")
    from sqlalchemy import event

    tracker = TaskTracker(store)
    await tracker.create("compile", task_id="task", **OWNER)
    transaction = store._transaction

    @asynccontextmanager
    async def cancel_before_commit():
        async with transaction() as session:

            def cancel(_session):
                raise asyncio.CancelledError

            event.listen(session.sync_session, "before_commit", cancel)
            yield session

    monkeypatch.setattr(store, "_transaction", cancel_before_commit)
    with store._subscribe_task("task", **OWNER) as changed:
        with pytest.raises(asyncio.CancelledError):
            await tracker.complete("task", {}, **OWNER)
        await asyncio.sleep(0)
        assert not changed.done()
        assert (await other.get_task("task", **OWNER)).status is TaskStatus.PENDING


@pytest.mark.asyncio
async def test_confirmed_commit_notifies_before_cancellation_escapes(task_stores, monkeypatch):
    store, other = task_stores
    tracker = TaskTracker(store)
    await tracker.create("compile", task_id="task", **OWNER)
    if isinstance(store, PersistentTaskStore):
        write = store._write_task
        committed, release = asyncio.Event(), asyncio.Event()

        async def delayed_write(task):
            await write(task)
            committed.set()
            await release.wait()

        monkeypatch.setattr(store, "_write_task", delayed_write)
    else:
        from sqlalchemy import event

        transaction = store._transaction

        @asynccontextmanager
        async def cancel_after_commit():
            async with transaction() as session:

                def cancel(_session):
                    asyncio.current_task().cancel()

                event.listen(session.sync_session, "after_commit", cancel, insert=True)
                yield session

        monkeypatch.setattr(store, "_transaction", cancel_after_commit)
    waiter = await _waiting_after_read(tracker, monkeypatch)
    writing = asyncio.create_task(tracker.complete("task", {"ok": True}, **OWNER))
    try:
        if isinstance(store, PersistentTaskStore):
            await asyncio.wait_for(committed.wait(), 2)
            writing.cancel()
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(writing, 2)
        assert (await asyncio.wait_for(waiter, 2)).status is TaskStatus.COMPLETED
        assert (await other.get_task("task", **OWNER)).status is TaskStatus.COMPLETED
        assert store._task_changes.waiter_count == 0
    finally:
        if isinstance(store, PersistentTaskStore):
            release.set()
        writing.cancel()
        waiter.cancel()
        await asyncio.gather(writing, waiter, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("writer_kind", ["independent-store", "process", "missed-notification"])
async def test_mysql_wait_reconciles_external_or_missed_commit(
    task_stores, monkeypatch, writer_kind
):
    store, other = task_stores
    if isinstance(store, PersistentTaskStore):
        pytest.skip("Independent durable writers require MySQL")
    reader = TaskTracker(store)
    await reader.create("compile", task_id="task", **OWNER)
    waiter = await _waiting_after_read(reader, monkeypatch, interval=0.02)
    try:
        if writer_kind == "process":
            code = """
import asyncio, json, sys
from openviking.service.task_store_mysql import MySQLTaskWorkStore
from openviking.service.task_tracker import TaskTracker
async def main():
    store = MySQLTaskWorkStore(params=json.load(sys.stdin))
    await store.load()
    try:
        await TaskTracker(store).complete('task', {'external': True}, account_id='account', user_id='user')
    finally:
        await store.close()
asyncio.run(main())
"""
            params = {
                **json.loads(os.environ["OV_TEST_MYSQL"]),
                "table": store._models.Task.__tablename__,
            }
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(params).encode()), 20
            )
            assert process.returncode == 0, (stdout, stderr)
        else:
            writer = store if writer_kind == "missed-notification" else other
            if writer is store:
                monkeypatch.setattr(store._task_changes, "notify", lambda *args: None)
            await TaskTracker(writer).complete("task", {"external": True}, **OWNER)
        assert (await asyncio.wait_for(waiter, 2)).result == {"external": True}
        assert store._task_changes.waiter_count == 0
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.fixture(params=["file", "mysql"])
async def task_stores(request):
    if request.param == "file":
        store = PersistentTaskStore(_FakeAgfs())
        yield store, store
        return
    raw = os.environ.get("OV_TEST_MYSQL")
    if not raw:
        pytest.skip("Set OV_TEST_MYSQL to exercise the shared Task/Work contract")
    sqlalchemy = pytest.importorskip("sqlalchemy.ext.asyncio")
    pytest.importorskip("aiomysql")
    from openviking.service import task_store_mysql

    params = {**json.loads(raw), "table": f"ov_contract_{uuid4().hex[:12]}"}
    first = task_store_mysql.MySQLTaskWorkStore(params=params)
    second = task_store_mysql.MySQLTaskWorkStore(params=params)
    admin = sqlalchemy.create_async_engine(first._url, **first._engine_options)
    sql_dir = Path(task_store_mysql.__file__).parent / "sql"
    schema_files = [
        sql_dir / "task_store_init.sql",
        sql_dir / "changes/0001_task_store_service_name.sql",
    ]
    try:
        async with admin.begin() as connection:
            for schema in schema_files:
                for statement in schema.read_text().replace("ov_tasks", params["table"]).split(";"):
                    if statement.strip():
                        await connection.exec_driver_sql(statement)
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
