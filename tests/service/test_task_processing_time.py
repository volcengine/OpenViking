# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Processing time measures the wall-clock union of worker-owned execution."""

import asyncio
from types import SimpleNamespace

import pytest

from openviking.service.task_domain import WorkRecord, processing_seconds
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.service.task_work_index import bind_task_context
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_executor import (
    SemanticTreeExecutor,
    SemanticTreeScheduler,
)
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from tests.test_task_tracker import _FakeAgfs

OWNER = {"account_id": "account", "user_id": "user"}


def test_work_timestamps_union_worker_occupancy_and_exclude_queue_time():
    works = [
        WorkRecord(
            work_id="first",
            task_id="task",
            queue_name="Semantic",
            started_at=10,
            finished_at=15,
        ),
        WorkRecord(
            work_id="second",
            task_id="task",
            queue_name="Embedding",
            started_at=12,
            finished_at=18,
        ),
        WorkRecord(
            work_id="third",
            task_id="task",
            queue_name="Inline",
            started_at=40,
            finished_at=43,
        ),
    ]

    assert processing_seconds(works, now=100) == 11


async def _create_work(
    tracker: TaskTracker,
    task_id: str,
    work_id: str = "worker",
) -> None:
    task = await tracker.create("add_resource", task_id=task_id, **OWNER)
    await tracker.start(task.task_id, **OWNER)
    await tracker.register_work(task_id, work_id, "Semantic", **OWNER)
    await tracker.start_work(task_id, work_id, **OWNER)


@pytest.mark.asyncio
async def test_saturated_dag_scheduler_counts_claimed_handler_time(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("openviking.service.task_store.time.time", lambda: now[0])
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    scheduler = SemanticTreeScheduler(max_workers=1)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_semantic_tree_scheduler",
        lambda _: scheduler,
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: None)
    entered = {name: asyncio.Event() for name in ["a", "b"]}
    release = {name: asyncio.Event() for name in ["a", "b"]}
    queued = asyncio.Event()
    executors = {}
    for name in ["a", "b"]:
        await _create_work(tracker, name)
        with bind_task_context(name, "account", "user"):
            executor = SemanticTreeExecutor(None, "resource", 1, None)
        executors[name] = executor

        async def node(_work, name=name, executor=executor):
            entered[name].set()
            await release[name].wait()
            executor._root_done.set()

        monkeypatch.setattr(executor, "_run_work_bound", node)
    schedule_b = executors["b"]._schedule_dir

    def queue_b(*args, **kwargs):
        schedule_b(*args, **kwargs)
        queued.set()

    monkeypatch.setattr(executors["b"], "_schedule_dir", queue_b)

    async def coordinate(name):
        try:
            await executors[name].run("viking://resources/" + name)
        finally:
            await tracker.mark_work_done(name, "worker", **OWNER)

    handlers = [asyncio.create_task(coordinate("a"))]
    try:
        await asyncio.wait_for(entered["a"].wait(), 2)
        handlers.append(asyncio.create_task(coordinate("b")))
        await asyncio.wait_for(queued.wait(), 2)
        assert not entered["b"].is_set()
        now[0] = 20
        assert (await tracker.get("a", **OWNER)).processing_seconds == 20
        assert (await tracker.get("b", **OWNER)).processing_seconds == 20
        release["a"].set()
        await asyncio.wait_for(entered["b"].wait(), 2)
        now[0] = 25
        assert (await tracker.get("b", **OWNER)).processing_seconds == 25
        release["b"].set()
        await asyncio.wait_for(asyncio.gather(*handlers), 2)
        assert (await tracker.get("b", **OWNER)).processing_seconds == 25
    finally:
        for event in release.values():
            event.set()
        workers = list(scheduler._workers)
        for task in [*handlers, *workers]:
            task.cancel()
        await asyncio.gather(*handlers, *workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_downstream_work_wait_remains_worker_occupied_time(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("openviking.service.task_store.time.time", lambda: now[0])
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    await _create_work(tracker, "task", "coordinator")
    await tracker.register_work("task", "embedding", "Embedding", **OWNER)

    entered = asyncio.Event()

    def keep_waiting():
        entered.set()
        return False

    now[0] = 3
    waiter = asyncio.create_task(
        tracker.wait_for_work("task", ["embedding"], timeout=2, stop_waiting=keep_waiting, **OWNER)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not waiter.done()
        now[0] = 20
        assert (await tracker.get("task", **OWNER)).processing_seconds == 20
        await tracker.mark_work_done("task", "embedding", **OWNER)
        await asyncio.wait_for(waiter, 1)
        now[0] = 22
        await tracker.mark_work_done("task", "coordinator", **OWNER)
        assert (await tracker.get("task", **OWNER)).processing_seconds == 22
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("context_type", ["resource", "skill"])
async def test_semantic_retry_cooldown_counts_full_worker_occupied_time(monkeypatch, context_type):
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    now = [0.0]
    monkeypatch.setattr("openviking.service.task_store.time.time", lambda: now[0])
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    await _create_work(tracker, "task")
    message = SemanticMsg(
        uri="viking://agent/skills/retry-test"
        if context_type == "skill"
        else "viking://resources/retry-test",
        context_type=context_type,
        **OWNER,
    )
    enqueued = []

    async def sleep(delay):
        assert delay == 30
        now[0] += delay
        assert (await tracker.get("task", **OWNER)).processing_seconds == 33

    async def enqueue(msg, *, deduplicate_memory):
        assert deduplicate_memory is False
        now[0] += 2
        enqueued.append(msg.id)

    queue = SimpleNamespace(enqueue=enqueue)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(SEMANTIC="semantic", get_queue=lambda _: queue),
    )
    processor = SemanticProcessor()
    breaker = processor._account_breaker(OWNER["account_id"])
    for _ in range(5):
        breaker.record_failure(TimeoutError("model timed out"))
    now[0] = 3
    with bind_task_context("task", **OWNER):
        result = await processor.on_dequeue(message.to_dict())
    assert result.outcome is ProcessOutcome.REQUEUED
    assert enqueued == [message.id]
    assert (await tracker.get("task", **OWNER)).processing_seconds == 35

    await tracker.mark_work_requeued("task", "worker", **OWNER)
    assert (await tracker.get("task", **OWNER)).processing_seconds is None
    now[0] = 50
    await tracker.start_work("task", "worker", **OWNER)
    now[0] = 55
    await tracker.mark_work_done("task", "worker", **OWNER)
    assert (await tracker.get("task", **OWNER)).processing_seconds == 5
