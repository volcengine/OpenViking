# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Observable Task/Work lifecycle and persistence contracts."""

import asyncio
from unittest.mock import Mock, create_autospec

import pytest

from openviking.service.task_domain import TaskStatus, WorkState
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


@pytest.mark.asyncio
async def test_requeue_keeps_logical_work_until_completion(tracker):
    assert await tracker.register_work("task", "work", "Semantic", **OWNER)
    assert (
        await tracker.register_work("task", "work", "Semantic", **OWNER)
        is WorkRegistration.EXISTING
    )
    await tracker.complete("task", {"done": True}, **OWNER)
    await tracker.start_work("task", "work", **OWNER)
    await tracker.mark_work_requeued("task", "work", **OWNER)
    assert (await tracker.get("task", **OWNER)).status is TaskStatus.RUNNING

    await tracker.start_work("task", "work", **OWNER)
    await tracker.mark_work_done("task", "work", **OWNER)
    completed = await tracker.wait("task", timeout=0, **OWNER)
    assert completed.status is TaskStatus.COMPLETED
    assert completed.result["queue_status"]["Semantic"] == {
        "processed": 1,
        "requeue_count": 1,
        "error_count": 0,
        "errors": [],
    }
    assert (
        await tracker.register_work("task", "late", "Semantic", **OWNER)
        is WorkRegistration.REJECTED
    )


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
