# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Store-backed regression for same-SHA recovery after a terminal index failure."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_types import WatchPatch, WatchRunResult, WatchTask
from tests.resource.test_watch_store import (  # noqa: F401
    OWNER,
    create,
)
from tests.resource.test_watch_store import (
    make_store as make_store,
)
from tests.resource.test_watch_store import (
    store as store,
)

REPO = "https://github.com/volcengine/OpenViking.git"
SHA = "a" * 40
CHECKPOINT = {"kind": "git", "head_sha": SHA, "instruction": ""}


@pytest.mark.asyncio
async def test_failed_same_sha_is_retried_and_success_resets_checkpoint(
    store, watch_vfs, watch_tracker, monkeypatch
):
    owner = {"account_id": OWNER.account_id, "user_id": OWNER.user_id}
    calls: list[dict[str, Any]] = []

    async def refresh(**kwargs):
        record = await watch_tracker.create(
            "add_resource",
            task_id=f"refresh-{len(calls)}",
            resource_id=kwargs["to"],
            **owner,
        )
        await kwargs["on_task_created"](record)
        calls.append(kwargs)
        return {"status": "success", "task_id": record.task_id}

    manager = WatchManager(
        store,
        viking_fs=watch_vfs,
        tracker=watch_tracker,
        resource_service=SimpleNamespace(refresh_resource=AsyncMock(side_effect=refresh)),
    )
    monkeypatch.setattr(manager, "_probe_git_checkpoint", AsyncMock(return_value=CHECKPOINT))
    target = "viking://resources/git-recovery"
    task = WatchTask.from_dict(
        {
            "account_id": OWNER.account_id,
            "user_id": OWNER.user_id,
            "to_uri": target,
            "path": REPO,
            "source_checkpoint": CHECKPOINT,
            "last_status": "failed",
            "last_error": "embedding Work failed",
            "last_task_id": "failed-import",
            "watch_interval": 1,
        }
    )
    task.next_execution_time = task.calculate_next_execution_time()
    await watch_vfs.mkdir(target, ctx=manager.context(task), exist_ok=True)
    await store.create_task(task)

    receipt = await manager.trigger(task.task_id)
    assert receipt is not None
    assert len(calls) == 1

    failed_result = {
        "task_id": receipt.task_id,
        "import_complete": True,
        "source_checkpoint": dict(CHECKPOINT),
    }
    await watch_tracker.fail(
        receipt.task_id, "embedding Work failed", result=failed_result, **owner
    )
    assert await manager.settle(task.task_id, receipt.task_id)
    failed = await store.get_task(task.task_id)
    assert failed.last_status == "failed"
    assert failed.recovery_attempts == 1

    recovery = await manager.trigger(task.task_id)
    assert recovery is not None
    assert len(calls) == 2
    await watch_tracker.complete(
        recovery.task_id,
        {
            "task_id": recovery.task_id,
            "import_complete": True,
            "meta": {"resolved_head_sha": SHA},
            "source_checkpoint": dict(CHECKPOINT),
        },
        **owner,
    )
    assert await manager.settle(task.task_id, recovery.task_id)
    recovered = await store.get_task(task.task_id)
    assert recovered.last_status == "completed"
    assert recovered.source_checkpoint == CHECKPOINT
    assert recovered.recovery_attempts == 0
    assert await manager.trigger(task.task_id) is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_recovery_attempts_persist_and_reset_after_reload(store):
    task = await create(store, due=True)
    candidate = await store.get_candidate(task.task_id)
    run = await store.begin_run(candidate, manual=True)
    assert run is not None
    assert await store.attach_task(task.task_id, "run-1", started_at=run.started_at)
    assert await store.finish_run(
        task.task_id,
        "run-1",
        WatchRunResult(status="failed", recovery_attempts=2),
        started_at=run.started_at,
    )
    saved = await store.get_task(task.task_id)
    assert saved.recovery_attempts == 2

    await store.close()
    await store.initialize()
    reloaded = await store.get_task(task.task_id)
    assert reloaded.recovery_attempts == 2

    candidate = await store.get_candidate(task.task_id)
    run = await store.begin_run(candidate, manual=True)
    assert run is not None
    assert await store.attach_task(task.task_id, "run-2", started_at=run.started_at)
    assert await store.finish_run(
        task.task_id,
        "run-2",
        WatchRunResult(status="completed", recovery_attempts=0),
        started_at=run.started_at,
    )
    completed = await store.get_task(task.task_id)
    assert completed.recovery_attempts == 0


@pytest.mark.asyncio
async def test_source_failure_without_checkpoint_still_persists_attempts(store):
    task = await create(store, due=True, source_checkpoint=dict(CHECKPOINT))
    candidate = await store.get_candidate(task.task_id)
    run = await store.begin_run(candidate, manual=True)
    assert run is not None
    assert await store.attach_task(task.task_id, "source-fail", started_at=run.started_at)
    assert await store.finish_run(
        task.task_id,
        "source-fail",
        WatchRunResult(status="failed", recovery_attempts=2),
        started_at=run.started_at,
    )
    saved = await store.get_task(task.task_id)
    assert saved.source_checkpoint == CHECKPOINT
    assert saved.recovery_attempts == 2

    await store.close()
    await store.initialize()
    reloaded = await store.get_task(task.task_id)
    assert reloaded.recovery_attempts == 2


@pytest.mark.asyncio
async def test_superseded_run_does_not_change_recovery_budget(store):
    task = await create(store, due=True, source_checkpoint=CHECKPOINT, recovery_attempts=1)
    run = await store.begin_run(await store.get_candidate(task.task_id), manual=True)
    assert run is not None
    assert await store.attach_task(task.task_id, "older", started_at=run.started_at)
    assert await store.patch_task(task.task_id, WatchPatch({"instruction": "new"}), OWNER)
    assert await store.finish_run(
        task.task_id,
        "older",
        WatchRunResult(status="failed", recovery_attempts=2),
        started_at=run.started_at,
    )
    assert (await store.get_task(task.task_id, OWNER)).recovery_attempts == 0

    newer = await store.begin_run(await store.get_candidate(task.task_id), manual=True)
    assert newer is not None
    assert await store.attach_task(task.task_id, "newer", started_at=newer.started_at)
    assert not await store.finish_run(
        task.task_id,
        "older",
        WatchRunResult(status="completed", recovery_attempts=2),
        started_at=run.started_at,
    )
    assert (await store.get_task(task.task_id, OWNER)).recovery_attempts == 0
