# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Watch store contracts on the file store and, when configured, MySQL.

In file mode every simulated node shares the one process's store. Set
OPENVIKING_WATCH_MYSQL_PARAMS to a JSON object of connection parameters for
an isolated MySQL database; the fixture provisions a randomly named table from
openviking/service/sql/001_watch_tasks.sql and drops them afterwards.
"""

import asyncio
import base64
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from openviking.resource.watch_store import WatchStore
from openviking.resource.watch_store_file import FileWatchStore
from openviking.resource.watch_store_mysql import MySQLWatchStore
from openviking.resource.watch_types import (
    PermissionDeniedError,
    WatchAuthority,
    WatchPatch,
    WatchRunResult,
    WatchTask,
)
from openviking_cli.exceptions import ConflictError
from tests.utils.watch_fixtures import local_viking_fs

MYSQL_PARAMS = os.environ.get("OPENVIKING_WATCH_MYSQL_PARAMS")
SCHEMA = Path(__file__).parents[2] / "openviking/service/sql/001_watch_tasks.sql"
ACCOUNT = "account-a"
OWNER = WatchAuthority(ACCOUNT, "user-a", "user")
CONNECTOR_AUTH = {"provider": "connector_encrypted", "ciphertext": "unused"}
ENCRYPTION_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


def _mysql_connection(params):
    import pymysql

    values = {key: value for key, value in params.items() if key != "maxsize"}
    return pymysql.connect(**values, charset="utf8mb4", autocommit=True)


@pytest_asyncio.fixture(params=["file", "mysql"])
async def make_store(request, tmp_path):
    """Return a factory for stores sharing one database (one per simulated node)."""
    created: list[WatchStore] = []
    cleanup = None
    if request.param == "file":
        viking_fs = local_viking_fs(tmp_path / "agfs")
    else:
        if not MYSQL_PARAMS:
            pytest.skip("OPENVIKING_WATCH_MYSQL_PARAMS is not configured")
        base = json.loads(MYSQL_PARAMS)
        table = "ov_test_watch_" + uuid.uuid4().hex[:12]
        params = {**base, "table": table, "credential_encryption_key": ENCRYPTION_KEY}

        def provision():
            connection = _mysql_connection(base)
            sql = SCHEMA.read_text()
            sql = sql[sql.index("CREATE TABLE") :].replace("ov_watch_tasks", table)
            with connection.cursor() as cursor:
                for statement in sql.split(";"):
                    if statement.strip():
                        cursor.execute(statement)
            return connection

        connection = await asyncio.to_thread(provision)

        def cleanup():
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE `{table}`")
            connection.close()

    async def factory(service_name: str = "watch-test", **overrides) -> WatchStore:
        if request.param == "file":
            if created:
                return created[0]
            store: WatchStore = FileWatchStore(viking_fs)
        else:
            store = MySQLWatchStore(service_name=service_name, params={**params, **overrides})
        await store.initialize()
        created.append(store)
        return store

    try:
        yield factory
    finally:
        for store in created:
            await store.close()
        if cleanup is not None:
            await asyncio.to_thread(cleanup)


@pytest_asyncio.fixture
async def store(make_store):
    return await make_store()


async def create(store, uri="viking://resources/a", *, initial_run_id=None, due=False, **values):
    values.setdefault("account_id", ACCOUNT)
    values.setdefault("user_id", "user-a")
    if due:
        values["created_at"] = datetime.now() - timedelta(minutes=5)
    task = WatchTask(path="https://example.org/doc", to_uri=uri, watch_interval=1, **values)
    task.next_execution_time = task.calculate_next_execution_time() if task.is_active else None
    await store.create_task(task, initial_run_id=initial_run_id)
    return task


@pytest.mark.asyncio
async def test_begin_run_is_exclusive_across_nodes(make_store):
    first, second = await make_store(), await make_store()
    task = await create(first, due=True)

    runs = await asyncio.gather(
        *[
            node.begin_run(
                task.task_id,
                run_id=f"run-{index}",
                target_uri=task.to_uri,
            )
            for index, node in enumerate((first, second) * 3)
        ]
    )

    winners = [run for run in runs if run is not None]
    assert len(winners) == 1
    state = await second.get_state(task.task_id)
    assert state.current_run_id == winners[0].run_id
    assert state.run_started_at is not None
    assert await first.due(10) == []
    assert [s.current_run_id for s in await second.running()] == [winners[0].run_id]


@pytest.mark.asyncio
async def test_begin_run_requires_due_enabled_and_unmoved_target(store):
    not_due = await create(store, "viking://resources/later")
    task = await create(store, due=True)

    assert (
        await store.begin_run(not_due.task_id, run_id="not-due", target_uri=not_due.to_uri) is None
    )
    assert (
        await store.begin_run(
            task.task_id,
            run_id="moved",
            target_uri="viking://resources/moved",
            manual=True,
        )
        is None
    )
    await store.patch_task(task.task_id, WatchPatch({"is_active": False}))
    assert await store.begin_run(task.task_id, run_id="paused", target_uri=task.to_uri) is None

    manual = await store.begin_run(
        task.task_id,
        run_id="manual",
        target_uri=task.to_uri,
        manual=True,
    )
    assert manual is not None and manual.run_id == "manual"


@pytest.mark.asyncio
async def test_deactivate_idle_skips_occupied_and_moved_watches(store):
    occupied = await create(store, "viking://resources/busy", initial_run_id="run-1")
    task = await create(store, due=True)

    assert not await store.deactivate_idle(
        occupied.task_id, target_uri=occupied.to_uri, error="gone"
    )
    assert not await store.deactivate_idle(
        task.task_id, target_uri="viking://resources/moved", error="gone"
    )
    assert await store.deactivate_idle(task.task_id, target_uri=task.to_uri, error="gone")

    assert not (await store.get_task(task.task_id)).is_active
    assert (await store.get_state(occupied.task_id)).current_run_id == "run-1"


@pytest.mark.asyncio
async def test_only_the_owning_run_can_settle_and_advance_its_cursor(store):
    task = await create(
        store, initial_run_id="run-1", auth_state=CONNECTOR_AUTH, connector_states={"cursor": 1}
    )

    result = WatchRunResult(
        status="completed",
        execution_task_id="run-1",
        connector_states={"cursor": 2},
        has_connector_states=True,
    )
    assert not await store.finish_run(task.task_id, "run-2", result)
    assert await store.fence_run(task.task_id, "run-1")
    assert (await store.get_state(task.task_id)).current_run_id == "run-1"
    assert await store.finish_run(
        task.task_id,
        "run-1",
        result,
    )
    assert not await store.finish_run(task.task_id, "run-1", WatchRunResult(status="failed"))

    state = await store.get_state(task.task_id)
    assert state.current_run_id is None
    assert state.task.last_status == "completed"
    assert state.task.last_task_id == "run-1"
    assert state.task.connector_states == {"cursor": 2}
    expected = state.task.last_execution_time.timestamp() + 60
    assert abs(state.task.next_execution_time.timestamp() - expected) < 1

    second = await store.begin_run(
        task.task_id,
        run_id="run-2",
        target_uri=task.to_uri,
        manual=True,
    )
    assert second is not None
    assert not await store.finish_run(
        task.task_id,
        "run-1",
        WatchRunResult(status="failed", error="stale"),
    )

    state = await store.get_state(task.task_id)
    assert state.current_run_id == "run-2"
    assert state.task.last_status == "completed"


@pytest.mark.asyncio
async def test_pause_wins_over_concurrent_run_settlement(store):
    task = await create(store, initial_run_id="run-1")
    await asyncio.gather(
        store.patch_task(task.task_id, WatchPatch({"is_active": False}), OWNER),
        store.finish_run(task.task_id, "run-1", WatchRunResult(status="completed")),
    )

    state = await store.get_state(task.task_id)
    assert state.task.is_active is False
    assert state.task.last_status == "completed"
    assert state.task.next_execution_time is None


@pytest.mark.asyncio
async def test_permissions(store):
    task = await create(store)
    intruder = WatchAuthority(ACCOUNT, "user-b", "admin")

    assert await store.get_task(task.task_id, intruder) is None
    with pytest.raises(PermissionDeniedError):
        await store.patch_task(task.task_id, WatchPatch({"reason": "x"}), intruder)
    with pytest.raises(PermissionDeniedError):
        await store.delete_task(task.task_id, intruder)
    assert (await store.patch_task(task.task_id, WatchPatch({"reason": "x"}), OWNER)).reason == "x"


@pytest.mark.asyncio
async def test_native_targets_remain_exclusive_within_their_account(store):
    await create(store)
    with pytest.raises(ConflictError):
        await create(store, auth_state=CONNECTOR_AUTH)
    await create(store, account_id="account-b")


@pytest.mark.asyncio
async def test_occupied_tombstone_is_hidden_and_purged_on_settlement(store):
    task = await create(store, initial_run_id="run-1")

    assert await store.delete_task(task.task_id, OWNER)
    assert await store.get_task(task.task_id) is None
    assert await store.list_tasks(OWNER) == []
    assert (
        await store.begin_run(
            task.task_id,
            run_id="deleted",
            target_uri=task.to_uri,
            manual=True,
        )
        is None
    )
    assert await store.has_running_overlap(ACCOUNT, [task.to_uri])

    await store.finish_run(task.task_id, "run-1", WatchRunResult(status="cancelled"))
    assert await store.get_state(task.task_id) is None


@pytest.mark.asyncio
async def test_delete_tasks_returns_runs_to_release(store):
    idle = await create(store, "viking://resources/idle")
    busy = await create(store, "viking://resources/busy", initial_run_id="run-1")
    other = await create(store, "viking://resources/other", user_id="user-b")

    running = await store.delete_tasks(ACCOUNT, "user-a")

    assert [(state.task.task_id, state.current_run_id) for state in running] == [
        (busy.task_id, "run-1")
    ]
    assert await store.get_state(idle.task_id) is None
    assert (await store.get_state(busy.task_id)).deleted
    assert await store.get_task(other.task_id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "uris", "expected"),
    [
        ("viking://resources/a/b", ["viking://resources/a"], True),
        ("viking://resources/a/b", ["viking://resources/a/b/c.md"], True),
        ("viking://resources/a/b", ["viking://resources/a/bc"], False),
        ("viking://resources/a_b", ["viking://resources/a%b"], False),
    ],
)
async def test_running_overlap_uses_path_boundaries(store, target, uris, expected):
    await create(store, target, initial_run_id="run-1")

    assert await store.has_running_overlap(ACCOUNT, uris) is expected
    assert await store.has_running_overlap("account-b", uris) is False


@pytest.mark.asyncio
async def test_scan_pages_do_not_lose_due_or_running_watches(store):
    tasks = [await create(store, f"viking://resources/{i}", due=True) for i in range(3)]
    due = await store.due(2)
    remaining = await store.due(2, offset=2)
    assert [state.task.task_id for state in due + remaining] == [task.task_id for task in tasks]
    for task in tasks:
        await store.begin_run(task.task_id, run_id=f"scan-{task.task_id}", target_uri=task.to_uri)
    page = await store.running(limit=2)
    remaining = await store.running(after=page[-1].task.task_id, limit=2)
    assert [state.task.task_id for state in page + remaining] == sorted(
        task.task_id for task in tasks
    )


@pytest.mark.asyncio
async def test_move_escapes_nothing_and_respects_boundaries(store):
    exact = await create(store, "viking://resources/a_b")
    child = await create(store, "viking://resources/a_b/c")
    lookalike = await create(store, "viking://resources/aXb")
    sibling = await create(store, "viking://resources/a_bc")

    moved = await store.move(
        account_id=ACCOUNT,
        from_uri="viking://resources/a_b",
        to_uri="viking://resources/z",
    )

    assert set(moved.task_ids) == {exact.task_id, child.task_id}
    assert (await store.get_task(child.task_id)).to_uri == "viking://resources/z/c"
    assert (await store.get_task(lookalike.task_id)).to_uri == "viking://resources/aXb"
    assert (await store.get_task(sibling.task_id)).to_uri == "viking://resources/a_bc"


@pytest.mark.asyncio
async def test_move_with_task_ids_is_exact_and_reversible(store):
    root = await create(store, "viking://resources/source")
    child = await create(store, "viking://resources/source/child")

    moved = await store.move(
        account_id=ACCOUNT,
        from_uri="viking://resources/source",
        to_uri="viking://resources/target",
        task_ids=[child.task_id],
    )
    assert moved.task_ids == (child.task_id,)
    assert (await store.get_task(root.task_id)).to_uri == "viking://resources/source"
    assert (await store.get_task(child.task_id)).to_uri == "viking://resources/target/child"

    restored = await store.move(
        account_id=ACCOUNT,
        from_uri="viking://resources/target",
        to_uri="viking://resources/source",
        task_ids=moved.task_ids,
    )
    assert restored.task_ids == moved.task_ids
    assert (await store.get_task(child.task_id)).to_uri == "viking://resources/source/child"


@pytest.mark.asyncio
async def test_move_rejects_incomplete_explicit_task_selection(store):
    task = await create(store, "viking://resources/source")

    with pytest.raises(ConflictError, match="source changed"):
        await store.move(
            account_id=ACCOUNT,
            from_uri="viking://resources/source",
            to_uri="viking://resources/target",
            task_ids=[task.task_id, "missing"],
        )

    assert (await store.get_task(task.task_id)).to_uri == "viking://resources/source"


@pytest.mark.asyncio
async def test_service_names_partition_rows(make_store):
    first = await make_store()
    if first.provider == "file":
        pytest.skip("the file store belongs to one deployment")
    other = await make_store("other-service")
    task = await create(first, initial_run_id="run-1")

    assert await other.get_state(task.task_id) is None
    assert await other.running() == []
    assert not await other.has_running_overlap(ACCOUNT, [task.to_uri])
    await create(other)


@pytest.mark.parametrize("key", ["not base64!", base64.b64encode(b"short").decode()])
def test_mysql_store_rejects_malformed_encryption_keys(key):
    with pytest.raises(ValueError, match="credential_encryption_key"):
        MySQLWatchStore(
            service_name="watch-test",
            params={
                "user": "u",
                "password": "p",
                "database": "d",
                "credential_encryption_key": key,
            },
        )


class TestFileRecovery:
    @pytest.mark.asyncio
    async def test_legacy_file_normalizes_active_paused_and_deleted_watches(self, watch_vfs):
        created = datetime.now() - timedelta(hours=1)
        legacy = {
            "path": "https://example.org/doc",
            "to_uri": "viking://resources/doc",
            "created_at": created.isoformat(),
            "watch_interval": 5,
        }
        await watch_vfs.write_file(
            FileWatchStore.STORAGE_URI,
            json.dumps(
                {
                    "tasks": [
                        {**legacy, "task_id": "active"},
                        {
                            **legacy,
                            "task_id": "paused",
                            "to_uri": "viking://resources/paused",
                            "is_active": False,
                            "next_execution_time": created.isoformat(),
                        },
                        {
                            **legacy,
                            "task_id": "deleted",
                            "to_uri": "viking://resources/deleted",
                            "deleted": True,
                        },
                    ]
                }
            ),
        )
        store = FileWatchStore(watch_vfs)
        await store.initialize()
        active = await store.get_task("active")
        assert active is not None
        assert active.next_execution_time == created + timedelta(minutes=5)
        assert active.processing_mode == "semantic_and_vectors"
        paused = await store.get_task("paused")
        assert paused is not None and paused.next_execution_time is None
        assert await store.get_state("deleted") is None
        saved = json.loads(await watch_vfs.read_file(FileWatchStore.STORAGE_URI))
        assert {task["task_id"] for task in saved["tasks"]} == {"active", "paused"}

    @pytest.mark.asyncio
    async def test_corrupt_primary_recovers_from_backup(self, watch_vfs, watch_store):
        task = await create(watch_store)
        await watch_store.patch_task(task.task_id, WatchPatch({"reason": "updated"}), OWNER)
        await watch_vfs.write_file(FileWatchStore.STORAGE_URI, "{not json")
        reopened = FileWatchStore(watch_vfs)
        await reopened.initialize()
        loaded = await reopened.get_task(task.task_id)
        assert loaded is not None and loaded.to_uri == task.to_uri
        saved = json.loads(await watch_vfs.read_file(FileWatchStore.STORAGE_URI))
        assert [task["task_id"] for task in saved["tasks"]] == [task.task_id]

    @pytest.mark.asyncio
    async def test_occupied_deletion_survives_restart_until_settlement(
        self, watch_vfs, watch_store
    ):
        task = await create(watch_store, initial_run_id="run-1")
        await watch_store.delete_task(task.task_id, OWNER)
        reopened = FileWatchStore(watch_vfs)
        await reopened.initialize()
        assert await reopened.get_task(task.task_id) is None
        assert await reopened.has_running_overlap(ACCOUNT, [task.to_uri])
        state = await reopened.get_state(task.task_id)
        assert state is not None and state.current_run_id == "run-1"
        await reopened.finish_run(task.task_id, "run-1", WatchRunResult(status="cancelled"))
        reloaded = FileWatchStore(watch_vfs)
        await reloaded.initialize()
        assert await reloaded.get_state(task.task_id) is None
        assert not await reloaded.has_running_overlap(ACCOUNT, [task.to_uri])

    @pytest.mark.asyncio
    async def test_can_persist_under_the_resources_tree_lock(self, watch_vfs, watch_manager):
        async with watch_manager.locks.tree("default", "viking://resources"):
            task = await create(watch_manager.store, initial_run_id="run-1")
            await watch_manager.finish_run(
                task.task_id, "run-1", WatchRunResult(status="completed")
            )
        reopened = FileWatchStore(watch_vfs)
        await reopened.initialize()
        loaded = await reopened.get_task(task.task_id)
        assert loaded is not None and loaded.last_status == "completed"
