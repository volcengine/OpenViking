"""Partial Runtime output must not retire unchanged, unprocessed sources."""

# ruff: noqa: F811 - imported pytest fixtures are reused as test parameters.

from copy import deepcopy

import pytest

from openviking.service.compile_targets import CompileLifecycle
from openviking.service.external_task_service import ExternalTaskSnapshot
from tests.service.test_compile_configuration_lifecycle import (
    SOURCE,
    make_task,
    registered,  # noqa: F401
    setup,  # noqa: F401
)

PARTIAL = {"completion_status": "partial"}
COUNTS = {"created": 1, "updated": 0, "deleted": 0}


async def test_partial_run_keeps_output_and_previous_source_commit(setup, registered):
    _, name, first = registered
    await setup.lifecycle.on_start(first)
    await setup.lifecycle.on_finish(first, ExternalTaskSnapshot(status="completed"))
    prior = await setup.store.get(first.account_id, "alice", name)
    source_commit = prior["progress"]["source_commit"]

    partial = make_task(setup, compile_name=name, record=prior, created_at=200)
    setup.fs.source_changes = [{"uri": SOURCE + "/unprocessed.md", "change_type": "added"}]
    assert await setup.lifecycle.on_start(partial) == "proceed"
    target = partial.meta["request"]["to"]
    setup.fs.output_changes = [{"uri": target + "/kept.md", "change_type": "added"}]
    snapshot = ExternalTaskSnapshot(status="completed", result=PARTIAL)
    outcome = await setup.lifecycle.on_finish(partial, snapshot)
    assert outcome.status == "completed"
    assert outcome.result == {**COUNTS, **PARTIAL}
    setup.fs.restore.assert_not_awaited()

    restarted = CompileLifecycle(setup.store, setup.fs)
    restored = deepcopy(setup.tracker.tasks[partial.task_id])
    replay = await restarted.on_finish(restored, ExternalTaskSnapshot(status="completed"))
    assert replay.result == outcome.result
    unchanged = await setup.store.get(first.account_id, "alice", name)
    assert unchanged["progress"]["source_commit"] == source_commit
    retry = make_task(setup, compile_name=name, record=unchanged, created_at=300)
    assert await restarted.on_start(retry) == "proceed"
    plan = await restarted.runtime_changes(
        retry.meta["request"], retry.account_id, retry.task_id, "alice"
    )
    assert plan["base"] == source_commit
    assert plan["diff_dirs"] == [SOURCE]


@pytest.mark.parametrize("interrupt", ["output_snapshot", "finished_checkpoint"])
async def test_partial_delivery_survives_finalization_interruption(
    setup, registered, monkeypatch, interrupt
):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    target = task.meta["request"]["to"]
    setup.fs.output_changes = [{"uri": target + "/kept.md", "change_type": "added"}]
    with monkeypatch.context() as patch:
        if interrupt == "output_snapshot":

            async def fail_snapshot(**kwargs):
                raise OSError("synthetic interruption")

            patch.setattr(setup.fs, "commit", fail_snapshot)
        else:
            checkpoint = setup.lifecycle._save_checkpoint

            async def fail_finished(record, changes):
                if changes.get("phase") == "finished":
                    raise OSError("synthetic interruption")
                await checkpoint(record, changes)

            patch.setattr(setup.lifecycle, "_save_checkpoint", fail_finished)
        with pytest.raises(OSError, match="synthetic interruption"):
            await setup.lifecycle.on_finish(
                task, ExternalTaskSnapshot(status="completed", result=PARTIAL)
            )

    restarted = CompileLifecycle(setup.store, setup.fs)
    restored = deepcopy(setup.tracker.tasks[task.task_id])
    recovered = await restarted.on_start(restored)
    assert recovered.status == "completed"
    outcome = await restarted.on_finish(restored, recovered)
    assert outcome.result == {**COUNTS, **PARTIAL}
    assert (await setup.store.get(task.account_id, "alice", name))["progress"] == {}


@pytest.mark.parametrize("runtime_result", [None, {"completion_status": "unknown"}])
async def test_runtime_without_partial_marker_retains_existing_behavior(
    setup, registered, runtime_result
):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    setup.fs.output_changes = [
        {"uri": task.meta["request"]["to"] + "/page.md", "change_type": "added"}
    ]
    result = await setup.lifecycle.on_finish(
        task, ExternalTaskSnapshot(status="completed", result=runtime_result)
    )
    assert result.result == COUNTS
    assert (await setup.store.get(task.account_id, "alice", name))["progress"]["source_commit"]
