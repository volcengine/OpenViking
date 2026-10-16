# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.service.task_work_index import QueueTaskMetadata, TaskWorkIndex
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.asyncio
async def test_wait_for_work_observes_only_explicit_ids():
    index = TaskWorkIndex()
    first = QueueTaskMetadata("task", "first")
    unrelated = QueueTaskMetadata("task", "unrelated")
    assert index.register("Semantic", first)
    assert index.register("Embedding", unrelated)

    index.record_result(first, "completed")
    await index.wait_for_work("task", ["first"], timeout=0)

    assert index.has_work("task")


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
async def test_task_work_wait_includes_recovered_and_later_enqueued_work(recovered):
    index = TaskWorkIndex()
    root = QueueTaskMetadata("task", "root")
    semantic = QueueTaskMetadata("task", "semantic")
    embedding = QueueTaskMetadata("task", "embedding")
    if recovered:
        index.rebuild(
            {
                "Root": [{"task_id": "task", "_task_work_id": "root"}],
                "Semantic": [{"task_id": "task", "_task_work_id": "semantic"}],
            }
        )
    else:
        assert index.register("Root", root)
        assert index.register("Semantic", semantic)
    waiting = asyncio.create_task(
        index.wait_for_task_work("task", exclude_work_id="root", poll_interval=0.001)
    )
    try:
        await asyncio.sleep(0)
        assert not waiting.done()
        assert index.register("Embedding", embedding)
        index.record_result(semantic, "completed")
        await index.prepare_ack("Semantic", semantic)
        with pytest.raises(TimeoutError):
            await index.wait_for_task_work("task", exclude_work_id="root", timeout=0)
        index.record_result(embedding, "completed")
        await index.prepare_ack("Embedding", embedding)
        await asyncio.wait_for(waiting, timeout=1)
        assert index.has_work("task")
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


@pytest.mark.asyncio
async def test_requeue_keeps_logical_work_until_replacement_ack():
    index = TaskWorkIndex()
    work = QueueTaskMetadata("task", "work")
    assert index.register("Semantic", work)
    assert index.register("Semantic", work)

    index.record_result(work, "requeued")
    await index.prepare_ack("Semantic", work)
    assert index.has_work("task")

    index.record_result(work, "completed")
    await index.wait_for_work("task", ["work"], timeout=0.1)
    assert index.queue_status("task")["Semantic"] == {
        "processed": 1,
        "requeue_count": 1,
        "error_count": 0,
        "errors": [],
    }

    await index.prepare_ack("Semantic", work)
    assert not index.has_work("task")


@pytest.mark.parametrize("errors", [None, ["first", "second"]])
def test_terminal_work_result_is_monotonic_and_idempotent(errors):
    index = TaskWorkIndex()
    work = QueueTaskMetadata("task", "work")
    assert index.register("Embedding", work)
    messages = errors or ["first"]
    error = "\n".join(messages)

    index.record_result(work, "failed", error=error, errors=errors)
    index.record_result(work, "completed")
    index.record_result(work, "failed", error="later failure")

    assert index.failure("task") == error
    assert index.queue_status("task")["Embedding"] == {
        "processed": 0,
        "requeue_count": 0,
        "error_count": len(messages),
        "errors": [{"message": message} for message in messages],
    }


@pytest.mark.asyncio
async def test_enqueue_failure_finalizes_task_with_same_error():
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    index = TaskWorkIndex()
    tracker.attach_work_index(index)
    task = await tracker.create("content_write", account_id="account", user_id="user")
    await tracker.start(task.task_id, account_id="account", user_id="user")
    work = QueueTaskMetadata(task.task_id, "embedding", "account", "user")
    assert index.register("Embedding", work)

    await index.fail_enqueue("Embedding", work, "embedding enqueue returned false")

    failed = await tracker.get(task.task_id, account_id="account", user_id="user")
    assert failed.status is TaskStatus.FAILED
    assert failed.error == "embedding enqueue returned false"


@pytest.mark.asyncio
async def test_deleting_task_releases_retained_work_status():
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    index = TaskWorkIndex()
    tracker.attach_work_index(index)
    owner = {"account_id": "account", "user_id": "user"}
    task = await tracker.create("content_write", **owner)
    await tracker.start(task.task_id, **owner)
    work = QueueTaskMetadata(task.task_id, "semantic", "account", "user")
    assert index.register("Semantic", work)

    await tracker.complete(
        task.task_id,
        {"done": True, "queue_status": tracker.build_queue_status(task.task_id)},
        **owner,
    )
    index.record_result(work, "completed")
    await index.prepare_ack("Semantic", work)
    assert tracker.build_queue_status(task.task_id)["Semantic"]["processed"] == 1
    completed = await tracker.get(task.task_id, **owner)
    assert completed.result["queue_status"]["Semantic"]["processed"] == 1

    assert await tracker.delete_user_tasks(**owner) == 1
    assert tracker.build_queue_status(task.task_id) == {
        "Semantic": {
            "processed": 0,
            "requeue_count": 0,
            "error_count": 0,
            "errors": [],
        },
        "Embedding": {
            "processed": 0,
            "requeue_count": 0,
            "error_count": 0,
            "errors": [],
        },
    }
