# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scoped SQL deletion preserves isolation, blockers and transaction atomicity."""

import sqlite3

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

from openviking.service.task_domain import TaskRecord, TaskStatus, WorkState
from openviking.service.task_store import StoreOperationCode
from openviking.service.task_store_sqlite import SQLiteTaskWorkStore


class _LimitedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Python 3.10 keeps the native limit; setlimit was added in Python 3.11.
        if hasattr(self, "setlimit"):
            self.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 64)


async def _seed(store, task_ids, *, account="account", user="user", active=False):
    for task_id in task_ids:
        assert (
            await store.create(
                TaskRecord(
                    task_id=task_id,
                    task_type="compile",
                    status=TaskStatus.PENDING if active else TaskStatus.COMPLETED,
                    account_id=account,
                    user_id=user,
                )
            )
        ).created

    async def add_works():
        async with store._transaction() as session:
            await session.execute(
                insert(store._models.Work),
                [
                    {
                        "service_name": store._service_name,
                        "task_id": task_id,
                        "work_id": "work",
                        "account_id": account,
                        "user_id": user,
                        "queue_name": "Semantic",
                        "state": WorkState.PENDING.value if active else WorkState.DONE.value,
                        "enqueued_at": 1,
                        "updated_at": 1,
                    }
                    for task_id in task_ids
                ],
            )

    await store._sql_runtime.run(add_works)


async def _snapshot(store):
    async def read():
        async with store._session_factory() as session:
            result = {}
            for model in (store._models.Task, store._models.Work, store._models.Event):
                rows = await session.execute(
                    select(model.__table__).order_by(*model.__table__.primary_key.columns)
                )
                result[model.__tablename__] = [dict(row._mapping) for row in rows]
            return result

    return await store._sql_runtime.run(read)


@pytest.fixture
async def scoped_store(tmp_path):
    params = {"path": str(tmp_path / "tasks.db"), "maxsize": 1}
    stores = [
        SQLiteTaskWorkStore(params=params, service_name=service) for service in ("target", "other")
    ]
    try:
        for store in stores:
            store._engine_options["connect_args"]["factory"] = _LimitedConnection
            await store.load()
        await _seed(stores[0], [f"task-{i:03}" for i in range(80)])
        await _seed(stores[0], ["other-user"], user="other")
        await _seed(stores[0], ["other-account"], account="other", active=True)
        # Identical task ID in a different service must not affect blockers or deletion.
        await _seed(stores[1], ["task-000"], active=True)
        yield stores[0]
    finally:
        for store in stores:
            await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["account", "user", "task"])
async def test_scoped_deletion_exceeds_variable_limit_and_preserves_other_owners(
    scoped_store, scope
):
    before = await _snapshot(scoped_store)
    targets = {f"task-{i:03}" for i in range(80)}
    kwargs = {"account_id": "account"}
    if scope == "account":
        targets.add("other-user")
    else:
        kwargs["user_id"] = "user"
    if scope == "task":
        kwargs["task_id"] = "task-000"
        targets = {"task-000"}

    # Confirm this connection really rejects the old expanded-ID query.
    async def exceed_limit():
        async with scoped_store._session_factory() as session:
            await session.execute(
                text("SELECT " + ",".join(":p" + str(i) for i in range(65))),
                {f"p{i}": i for i in range(65)},
            )

    if hasattr(sqlite3.Connection, "setlimit"):
        with pytest.raises(OperationalError, match="too many SQL variables"):
            await scoped_store._sql_runtime.run(exceed_limit)

    result = await scoped_store.delete_tasks(**kwargs)
    assert result.accepted and not result.blockers
    assert {task.task_id for task in result.deleted} == targets
    assert all(task.account_id == "account" for task in result.deleted)
    expected = {
        name: [
            row
            for row in rows
            if not (row["service_name"] == "target" and row["task_id"] in targets)
        ]
        for name, rows in before.items()
    }
    assert await _snapshot(scoped_store) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["active_task", "pending_work", "running_work"])
async def test_scoped_deletion_rejects_blockers_without_partial_cleanup(scoped_store, blocker):
    async def add_blocker():
        async with scoped_store._transaction() as session:
            model = (
                scoped_store._models.Task if blocker == "active_task" else scoped_store._models.Work
            )
            values = (
                {"status": TaskStatus.PENDING.value}
                if blocker == "active_task"
                else {
                    "state": (
                        WorkState.PENDING.value
                        if blocker == "pending_work"
                        else WorkState.RUNNING.value
                    )
                }
            )
            await session.execute(
                update(model)
                .where(model.service_name == "target", model.task_id == "task-000")
                .values(**values)
            )

    await scoped_store._sql_runtime.run(add_blocker)
    before = await _snapshot(scoped_store)
    result = await scoped_store.delete_tasks(account_id="account", user_id="user")
    assert not result.accepted and not result.deleted
    assert [item.task_id for item in result.blockers] == ["task-000"]
    assert result.result.code is (
        StoreOperationCode.ACTIVE_TASK if blocker == "active_task" else StoreOperationCode.OPEN_WORK
    )
    assert await _snapshot(scoped_store) == before
    if blocker == "active_task":
        result = await scoped_store.delete_tasks(
            account_id="account", user_id="user", allow_active_types={"compile"}
        )
        assert result.accepted and len(result.deleted) == 80


@pytest.mark.asyncio
async def test_scoped_deletion_rolls_back_work_and_events_when_task_delete_fails(scoped_store):
    async def reject_task_delete():
        async with scoped_store._transaction() as session:
            await session.execute(
                text(
                    "CREATE TRIGGER reject_task_delete BEFORE DELETE ON ov_tasks "
                    "BEGIN SELECT RAISE(ABORT, 'injected task deletion failure'); END"
                )
            )

    await scoped_store._sql_runtime.run(reject_task_delete)
    before = await _snapshot(scoped_store)
    with pytest.raises(IntegrityError, match="injected task deletion failure"):
        await scoped_store.delete_tasks(account_id="account", user_id="user")
    assert await _snapshot(scoped_store) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [None, "user"])
async def test_sqlite_deletes_32764_tasks_and_their_dependent_records(tmp_path, user_id):
    store = SQLiteTaskWorkStore(params={"path": str(tmp_path / "tasks.db"), "maxsize": 1})
    count = 32_764
    try:
        await store.load()

        async def seed():
            common = {
                "service_name": store._service_name,
                "account_id": "account",
                "user_id": "user",
            }
            async with store._transaction() as session:
                await session.execute(
                    insert(store._models.Task),
                    [
                        dict(
                            common,
                            task_id=str(i),
                            task_type="compile",
                            status=TaskStatus.COMPLETED.value,
                            created_at=1,
                            updated_at=1,
                        )
                        for i in range(count)
                    ],
                )
                await session.execute(
                    insert(store._models.Work),
                    [
                        dict(
                            common,
                            task_id=str(i),
                            work_id="work",
                            queue_name="Semantic",
                            state=WorkState.DONE.value,
                            enqueued_at=1,
                            updated_at=1,
                        )
                        for i in range(count)
                    ],
                )
                await session.execute(
                    insert(store._models.Event),
                    [
                        {
                            "service_name": store._service_name,
                            "task_id": str(i),
                            "seq": 1,
                            "recorded_at": "2026-11-19T00:00:00+00:00",
                            "kind": "created",
                            "status": TaskStatus.COMPLETED.value,
                            "dropped_count": 0,
                            "started_mid_task": False,
                        }
                        for i in range(count)
                    ],
                )

        await store._sql_runtime.run(seed)
        result = await store.delete_tasks(account_id="account", user_id=user_id)
        assert result.accepted and len(result.deleted) == count

        async def verify():
            async with store._session_factory() as session:
                for model in (store._models.Task, store._models.Work, store._models.Event):
                    assert await session.scalar(select(func.count()).select_from(model)) == 0

        await store._sql_runtime.run(verify)
    finally:
        await store.close()
