"""Compile finalization consumes exact durable content Tasks, not queue idleness."""

# ruff: noqa: F811 - imported pytest fixtures are reused as parameters.

from copy import deepcopy

import pytest

from openviking.service.compile_targets import CompileLifecycle
from openviking.service.external_task_service import ExternalTaskSnapshot
from openviking.service.task_domain import TaskRecord, TaskStatus
from tests.service.test_compile_configuration_lifecycle import registered, setup  # noqa: F401

TARGET = "viking://resources/output"
RECEIPT = {"task_id": "write-exact", "uri": TARGET, "kind": "content_batch_write"}
PUBLICATION = {
    "protocol": "content_task_receipts_v1",
    "mode": "async",
    "receipts": [RECEIPT],
    "incomplete": False,
}


def dependency(setup, **updates):
    task = TaskRecord(
        task_id="write-exact",
        task_type="content_batch_write",
        account_id="acme",
        user_id="alice",
        resource_id=TARGET,
        status=TaskStatus.RUNNING,
    )
    for key, value in updates.items():
        setattr(task, key, value)
    setup.tracker.tasks[task.task_id] = deepcopy(task)
    return task


async def test_pending_exact_processing_defers_compile_and_survives_restart(setup, registered):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    content = dependency(setup)
    setup.fs.output_changes = [{"uri": TARGET + "/kept.md", "change_type": "added"}]
    pending = await setup.lifecycle.on_finish(
        task, ExternalTaskSnapshot(status="completed", result={"publication": PUBLICATION})
    )
    assert pending.status == "running" and pending.stage == "indexing"
    assert (await setup.lifecycle._read_run(task))["finished_at"] is None
    assert (await setup.store.get("acme", "alice", name))["progress"] == {}
    # A later unrelated Task cannot replace the persisted receipt set.
    dependency(setup, task_id="unrelated", status=TaskStatus.COMPLETED)
    restored = deepcopy(setup.tracker.tasks[task.task_id])
    lifecycle = CompileLifecycle(setup.store, setup.fs)
    again = await lifecycle.on_finish(
        restored,
        ExternalTaskSnapshot(
            status="completed",
            result={
                "publication": {**PUBLICATION, "receipts": [{**RECEIPT, "task_id": "unrelated"}]}
            },
        ),
    )
    assert again.status == "running"
    content.status = TaskStatus.COMPLETED
    setup.tracker.tasks[content.task_id] = content
    result = await lifecycle.on_finish(restored, ExternalTaskSnapshot(status="completed"))
    assert result.status == "completed"
    assert result.result["created"] == 1
    assert result.result["publication"]["state"] == "complete"
    assert result.result["publication"]["receipts"] == [RECEIPT]
    assert (await setup.store.get("acme", "alice", name))["progress"]


@pytest.mark.parametrize("status", [TaskStatus.FAILED, TaskStatus.CANCELLED])
async def test_failed_or_cancelled_processing_retains_outputs_without_retiring_sources(
    setup, registered, status
):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    content = dependency(setup, status=status, error="synthetic detail not copied")
    setup.fs.output_changes = [{"uri": TARGET + "/kept.md", "change_type": "added"}]
    result = await setup.lifecycle.on_finish(
        task, ExternalTaskSnapshot(status="completed", result={"publication": PUBLICATION})
    )
    assert result.status == "completed" and result.result["created"] == 1
    assert result.result["publication"]["state"] == "failed"
    assert "synthetic detail" not in str(result.result)
    assert (await setup.store.get("acme", "alice", name))["progress"] == {}
    # Terminal publication evidence is immutable even if a bad late callback lies.
    content.status = TaskStatus.COMPLETED
    setup.tracker.tasks[content.task_id] = content
    replay = await CompileLifecycle(setup.store, setup.fs).on_finish(
        task, ExternalTaskSnapshot(status="completed")
    )
    assert replay.result == result.result
    assert (await setup.store.get("acme", "alice", name))["progress"] == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"account_id": "other"},
        {"user_id": "other"},
        {"task_type": "compile"},
        {"resource_id": "viking://resources/elsewhere"},
        {"resource_id": None},
    ],
)
async def test_unrelated_dependency_is_unknown_and_never_satisfies_progress(
    setup, registered, changes
):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    dependency(setup, status=TaskStatus.COMPLETED, **changes)
    result = await setup.lifecycle.on_finish(
        task, ExternalTaskSnapshot(status="completed", result={"publication": PUBLICATION})
    )
    assert result.result["publication"]["state"] == "unknown"
    assert (await setup.store.get("acme", "alice", name))["progress"] == {}


async def test_legacy_missing_receipts_keep_existing_progress_behavior(setup, registered):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    result = await setup.lifecycle.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert result.status == "completed"
    assert "publication" not in result.result
    assert (await setup.store.get("acme", "alice", name))["progress"]


async def test_released_output_lock_does_not_replace_failed_processing_evidence(setup, registered):
    # SnapshotFS has no outstanding content lease; only the exact Task reveals failure.
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    dependency(
        setup,
        status=TaskStatus.COMPLETED,
        result={"queue_status": {"Embedding": {"errors": [{"message": "synthetic failure"}]}}},
    )
    result = await setup.lifecycle.on_finish(
        task, ExternalTaskSnapshot(status="completed", result={"publication": PUBLICATION})
    )
    assert result.result["publication"]["state"] == "failed"
    assert (await setup.store.get("acme", "alice", name))["progress"] == {}


@pytest.mark.parametrize(
    "queue_status",
    [
        {"Semantic": {"error_count": 1, "errors": []}},
        {"Embedding": {"error_count": "1"}},
    ],
)
async def test_processing_error_counts_cannot_be_ignored_when_details_are_bounded(
    setup, registered, queue_status
):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    dependency(setup, status=TaskStatus.COMPLETED, result={"queue_status": queue_status})
    result = await setup.lifecycle.on_finish(
        task, ExternalTaskSnapshot(status="completed", result={"publication": PUBLICATION})
    )
    assert result.result["publication"]["state"] == "failed"
    assert (await setup.store.get("acme", "alice", name))["progress"] == {}


async def test_missing_supported_receipt_never_silently_becomes_legacy(setup, registered):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    result = await setup.lifecycle.on_finish(
        task, ExternalTaskSnapshot(status="completed", result={"publication": PUBLICATION})
    )
    assert result.result["publication"]["state"] == "unknown"
    assert result.result["publication"]["reason_codes"] == ["dependency_missing"]
    assert (await setup.store.get("acme", "alice", name))["progress"] == {}


async def test_no_write_claim_with_versioned_changes_cannot_retire_sources(setup, registered):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    setup.fs.output_changes = [{"uri": TARGET + "/unexpected.md", "change_type": "added"}]
    result = await setup.lifecycle.on_finish(
        task,
        ExternalTaskSnapshot(
            status="completed",
            result={
                "publication": {
                    "protocol": PUBLICATION["protocol"],
                    "mode": "no_writes",
                    "receipts": [],
                }
            },
        ),
    )
    assert result.result["created"] == 1
    assert result.result["publication"]["state"] == "unknown"
    assert result.result["publication"]["reason_codes"] == ["unreceipted_target_changes"]
    assert (await setup.store.get("acme", "alice", name))["progress"] == {}


async def test_cancel_during_pending_processing_uses_existing_rollback_owner(setup, registered):
    _, name, task = registered
    await setup.lifecycle.on_start(task)
    dependency(setup)
    snapshot = ExternalTaskSnapshot(status="completed", result={"publication": PUBLICATION})
    assert (await setup.lifecycle.on_finish(task, snapshot)).status == "running"
    setup.tracker.cancelled.add(task.task_id)
    result = await setup.lifecycle.on_finish(task, snapshot)
    assert result.status == "cancelled"
    setup.fs.restore.assert_awaited_once()
    assert (await setup.store.get("acme", "alice", name))["progress"] == {}
