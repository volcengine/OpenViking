"""Partial Runtime output must not retire unchanged, unprocessed sources."""

# ruff: noqa: F811 - imported pytest fixtures are reused as test parameters.

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from openviking.service.compile_targets import CompileLifecycle
from openviking.service.external_task_service import ExternalTaskSnapshot
from tests.service.test_compile_configuration_lifecycle import registered, setup  # noqa: F401


@pytest.mark.parametrize(
    "result",
    [
        {
            "completion_status": "partial",
            "coverage_kind": "assignment_delivery_v1",
            "source_coverage": {"delivered": 1, "failed": 1},
            "failure_summary": ["source_read_failed"],
        },
        {},
        {"completion_status": "complete"},
    ],
)
async def test_partial_or_unknown_runtime_delivery_keeps_baseline_after_restart(
    setup, registered, result
):
    _, name, task = registered
    task.meta["request"]["compile"]["require_delivery_proof"] = True
    setup.tracker.tasks[task.task_id] = deepcopy(task)
    await setup.lifecycle.on_start(task)
    target = task.meta["request"]["to"]
    setup.fs.output_changes = [{"uri": target + "/kept.md", "change_type": "added"}]
    outcome = await setup.lifecycle.on_finish(
        task, ExternalTaskSnapshot(status="completed", result=result)
    )
    assert outcome.status == "completed"
    assert (await setup.store.get(task.account_id, "alice", name))["progress"] == {}
    assert outcome.result["created"] == 1
    assert outcome.result["completion_status"] == (
        "partial" if result.get("completion_status") == "partial" else "unknown"
    )
    restarted = CompileLifecycle(setup.store, setup.fs)
    restored = deepcopy(setup.tracker.tasks[task.task_id])
    replay = await restarted.on_finish(restored, ExternalTaskSnapshot(status="completed"))
    assert replay.result == outcome.result
    assert (await setup.store.get(task.account_id, "alice", name))["progress"] == {}


COMPLETE = {
    "completion_status": "complete",
    "coverage_kind": "assignment_delivery_v1",
    "source_coverage": {"delivered": 1},
    "failure_summary": [],
}


async def test_explicit_complete_advances_once_and_replays_durable_classification(
    setup, registered
):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    first = await setup.lifecycle.on_finish(
        task, ExternalTaskSnapshot(status="completed", result=COMPLETE)
    )
    run = await setup.lifecycle._read_run(task)
    assert first.result["completion_status"] == "complete"
    expected = (await setup.store.get(task.account_id, "alice", name))["progress"]
    assert expected["source_commit"] == run["meta"]["start_snapshot"]
    restored = deepcopy(setup.tracker.tasks[task.task_id])
    replay = await CompileLifecycle(setup.store, setup.fs).on_finish(
        restored, ExternalTaskSnapshot(status="completed")
    )
    assert replay.result == first.result
    assert (await setup.store.get(task.account_id, "alice", name))["progress"] == expected


async def test_partial_classification_survives_interruption_before_finish_snapshot(
    setup, registered, monkeypatch
):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    original = setup.fs.commit

    async def unavailable(**kwargs):
        raise OSError("synthetic snapshot unavailable")

    monkeypatch.setattr(setup.fs, "commit", unavailable)
    partial = {
        **COMPLETE,
        "completion_status": "partial",
        "source_coverage": {"delivered": 1, "failed": 1},
    }
    with pytest.raises(OSError, match="snapshot unavailable"):
        await setup.lifecycle.on_finish(
            task, ExternalTaskSnapshot(status="completed", result=partial)
        )
    persisted = await setup.lifecycle._read_run(task)
    assert persisted["meta"]["delivery"]["completion_status"] == "partial"
    assert persisted["finished_at"] is None
    monkeypatch.setattr(setup.fs, "commit", original)
    resumed = deepcopy(setup.tracker.tasks[task.task_id])
    result = await CompileLifecycle(setup.store, setup.fs).on_finish(
        resumed, ExternalTaskSnapshot(status="completed", result=COMPLETE)
    )
    assert result.result["completion_status"] == "partial"
    assert (await setup.store.get(task.account_id, "alice", name))["progress"] == {}


async def test_finished_partial_survives_failed_terminal_checkpoint(setup, registered, monkeypatch):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    patch = setup.lifecycle._save_checkpoint

    async def fail_terminal(task, updates):
        if updates.get("phase") == "finished":
            raise OSError("synthetic checkpoint unavailable")
        await patch(task, updates)

    monkeypatch.setattr(setup.lifecycle, "_save_checkpoint", fail_terminal)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        await setup.lifecycle.on_finish(
            task,
            ExternalTaskSnapshot(
                status="completed", result={**COMPLETE, "completion_status": "partial"}
            ),
        )
    restored = deepcopy(setup.tracker.tasks[task.task_id])
    result = await CompileLifecycle(setup.store, setup.fs).on_finish(
        restored, ExternalTaskSnapshot(status="completed", result=COMPLETE)
    )
    assert result.result["completion_status"] == "partial"
    assert (await setup.store.get(task.account_id, "alice", name))["progress"] == {}


async def test_legacy_finished_run_keeps_compatibility_without_claiming_verified_complete(
    setup, registered
):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    run = await setup.lifecycle._read_run(task)
    run.update(
        status="completed", finished_at=1000, result={"created": 1, "updated": 0, "deleted": 0}
    )
    await setup.lifecycle._save_run(task, run)
    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert result.result["completion_status"] == "unknown"
    assert result.result["created"] == 1
    assert (await setup.store.get(task.account_id, "alice", name))["progress"][
        "source_commit"
    ] == run["meta"]["start_snapshot"]


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("declared", ["partial", "unknown"])
async def test_frozen_run_policy_survives_worker_restart(setup, registered, strict, declared):
    _, name, task = registered
    task.meta["request"]["compile"]["require_delivery_proof"] = strict
    setup.tracker.tasks[task.task_id] = deepcopy(task)
    await setup.lifecycle.on_start(task)
    assert (await setup.lifecycle._read_run(task))["meta"]["require_delivery_proof"] is strict
    # A replacement worker consumes the accepted Task snapshot, without a live config lookup.
    restored = deepcopy(setup.tracker.tasks[task.task_id])
    status = await CompileLifecycle(setup.store, setup.fs).on_finish(
        restored, ExternalTaskSnapshot(status="completed", result={"completion_status": declared})
    )
    assert status.result["completion_status"] == declared
    progress = (await setup.store.get(task.account_id, "alice", name))["progress"]
    assert bool(progress) == (not strict and declared == "unknown")


@pytest.mark.parametrize("strict", [False, True])
async def test_transient_run_saves_policy_and_partial_without_compile_storage(setup, strict):
    from tests.service.test_compile_configuration_lifecycle import make_task

    task = make_task(setup)
    task.meta["request"]["compile"]["require_delivery_proof"] = strict
    setup.tracker.tasks[task.task_id] = deepcopy(task)
    await setup.lifecycle.on_start(task)
    original = setup.fs.commit
    setup.fs.commit = AsyncMock(side_effect=OSError("synthetic pause before snapshot"))
    with pytest.raises(OSError):
        await setup.lifecycle.on_finish(
            task, ExternalTaskSnapshot(status="completed", result={"completion_status": "partial"})
        )
    setup.fs.commit = original
    restored = deepcopy(setup.tracker.tasks[task.task_id])
    assert restored.auth["compile_execution"]["snapshots"]["require_delivery_proof"] is strict
    outcome = await CompileLifecycle(setup.store, setup.fs).on_finish(
        restored, ExternalTaskSnapshot(status="completed", result=COMPLETE)
    )
    assert outcome.result["completion_status"] == "partial"
