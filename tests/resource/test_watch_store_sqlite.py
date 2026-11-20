# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite durability, database isolation, and real multiprocess reservations."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.exc import IntegrityError

from openviking.resource.watch_store_sqlite import SQLiteWatchStore
from openviking.resource.watch_types import WatchPatch, WatchRunResult, WatchTask
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.storage_config import StorageConfig, WatchStoreConfig
from tests.resource.test_watch_process import probe
from tests.resource.test_watch_store import ENCRYPTION_KEY, OWNER, create


def make_store(path: Path, **params):
    return SQLiteWatchStore(
        service_name="sqlite-watch",
        params={"path": str(path), "credential_encryption_key": ENCRYPTION_KEY, **params},
    )


@pytest.mark.asyncio
async def test_restart_preserves_occupancy_checkpoint_and_tombstone(tmp_path):
    path = tmp_path / "nested" / "watch.db"
    store = make_store(path)
    await store.initialize()
    task = await create(store, due=True, source_checkpoint={"cursor": "old"})
    run = await store.begin_run(await store.get_candidate(task.task_id))
    assert await store.attach_task(task.task_id, "real-task", started_at=run.started_at)
    await store.delete_task(task.task_id, OWNER)
    await store.close()
    restarted = make_store(path)
    await restarted.initialize()
    try:
        state = await restarted.get_state(task.task_id)
        assert state.deleted
        assert state.current_run_id == "real-task"
        assert state.run_started_at == run.started_at
        assert state.task.source_checkpoint == {"cursor": "old"}
        assert await restarted.get_task(task.task_id) is None
        assert await restarted.finish_run(
            task.task_id, "real-task", WatchRunResult(status="completed")
        )
        assert await restarted.get_state(task.task_id) is None
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_start_is_exclusive_across_processes(tmp_path):
    import json

    store = make_store(tmp_path / "watch.db")
    await store.initialize()
    try:
        task = await create(store, due=True)
        config = {
            "service_name": store._service_name,
            "params": {
                "path": store._url.database,
                "credential_encryption_key": ENCRYPTION_KEY,
            },
            "watch_id": task.task_id,
        }
        results = await asyncio.gather(*[probe("sqlite", json.dumps(config)) for _ in range(4)])
        assert sum(result["won"] for result in results) == 1
        state = await store.get_state(task.task_id)
        assert state.run_started_at is not None
        assert state.current_run_id is None
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt", range(5))
async def test_concurrent_patches_preserve_both_updates(tmp_path, attempt):
    first, second = make_store(tmp_path / "watch.db"), make_store(tmp_path / "watch.db")
    await asyncio.gather(first.initialize(), second.initialize())
    try:
        task = await create(first)
        await asyncio.gather(
            first.patch_task(task.task_id, WatchPatch({"reason": "new reason"})),
            second.patch_task(task.task_id, WatchPatch({"instruction": "new instruction"})),
        )
        stored = await first.get_task(task.task_id)
        assert (stored.reason, stored.instruction) == ("new reason", "new instruction")
    finally:
        await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_private_state_is_encrypted_and_listable_with_wrong_key(tmp_path):
    path = tmp_path / "watch.db"
    store = make_store(path)
    await store.initialize()
    try:
        task = await create(
            store,
            auth_state={"token": "secret-token"},
            connector_states={"private": "secret-state"},
        )
        with sqlite3.connect(path) as connection:
            auth, states = connection.execute(
                "SELECT auth_state, connector_states FROM ov_watch_tasks"
            ).fetchone()
            assert auth.startswith("aesgcm:v1:") and "secret-token" not in auth
            assert states.startswith("aesgcm:v1:") and "secret-state" not in states
        # Use a valid but different 32-byte key.
        import base64

        other = make_store(path, credential_encryption_key=base64.b64encode(b"z" * 32).decode())
        await other.initialize()
        try:
            assert (await other.list_tasks_page(OWNER)).tasks == [task.to_dict()]
            with pytest.raises(RuntimeError, match="Cannot decrypt"):
                await other.get_task(task.task_id)
        finally:
            await other.close()
    finally:
        await store.close()


def test_config_uses_workspace_default_without_redis(tmp_path):
    config = OpenVikingConfig.from_dict(
        {
            "storage": {
                "workspace": str(tmp_path),
                "watch_store": {
                    "provider": " SQLite ",
                    "params": {"credential_encryption_key": ENCRYPTION_KEY},
                },
            }
        }
    )
    assert config.storage.agfs.coordination.provider == "memory"
    store = config.storage.build_watch_store(service_name="sqlite-watch", viking_fs=None)
    assert store.provider == "sqlite"
    assert store._url.database == str(tmp_path / "_system" / "watch" / "watch.db")
    override = tmp_path / "local" / "override.db"
    config = StorageConfig(
        workspace=str(tmp_path),
        watch_store={
            "provider": "sqlite",
            "params": {
                "path": str(override),
                "credential_encryption_key": ENCRYPTION_KEY,
            },
        },
    )
    assert config.build_watch_store(
        service_name="sqlite-watch", viking_fs=None
    )._url.database == str(override)


@pytest.mark.parametrize(
    "params",
    [
        {"path": "relative.db"},
        {"path": ":memory:"},
        {"path": ""},
        {"busy_timeout_ms": 0},
        {"busy_timeout_ms": True},
        {"busy_timeout_ms": "5000"},
        {"host": "localhost"},
    ],
)
def test_config_rejects_invalid_sqlite_parameters(params):
    with pytest.raises(ValueError):
        WatchStoreConfig(provider="sqlite", params=params)


def test_config_requires_encryption_key(tmp_path):
    with pytest.raises(ValueError, match="credential_encryption_key"):
        OpenVikingConfig.from_dict(
            {"storage": {"workspace": str(tmp_path), "watch_store": {"provider": "sqlite"}}}
        )


def test_duplicate_detection_does_not_mask_other_constraint_errors():
    duplicate = IntegrityError("", {}, sqlite3.IntegrityError("UNIQUE constraint failed: x.id"))
    not_null = IntegrityError("", {}, sqlite3.IntegrityError("NOT NULL constraint failed: x.id"))
    assert SQLiteWatchStore._is_duplicate_error(duplicate)
    assert not SQLiteWatchStore._is_duplicate_error(not_null)


@pytest.mark.asyncio
async def test_check_violation_propagates_and_releases_transaction(tmp_path):
    store = make_store(tmp_path / "watch.db")
    store._tasks.append_constraint(CheckConstraint("original_role <> 'invalid'"))
    await store.initialize()
    try:
        task = WatchTask(
            task_id="invalid-id",
            path="https://example.org/doc",
            to_uri="viking://resources/invalid",
            account_id=OWNER.account_id,
            user_id=OWNER.user_id,
            original_role="user",
        ).model_copy(update={"original_role": "invalid"})
        with pytest.raises(IntegrityError, match="CHECK constraint failed"):
            await store.create_task(task)
        assert await store.get_task(task.task_id) is None
        await create(store, task.to_uri, task_id=task.task_id)
        assert await store.get_task(task.task_id) is not None
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [{"unknown": "value"}, {"path": ""}, {"watch_interval": 0}])
async def test_invalid_patch_is_rejected_before_database_access(tmp_path, values):
    store = make_store(tmp_path / "watch.db")
    with pytest.raises(ValueError):
        await store.patch_task("missing", WatchPatch(values))
    assert not (tmp_path / "watch.db").exists()
