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
from openviking.storage.queuefs.semantic_executor import (
    SemanticTreeExecutor,
    SemanticTreeScheduler,
)
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

    first = asyncio.create_task(coordinate("a"))
    await entered["a"].wait()
    second = asyncio.create_task(coordinate("b"))
    await queued.wait()
    now[0] = 20
    assert (await tracker.get("a", **OWNER)).processing_seconds == 20
    assert (await tracker.get("b", **OWNER)).processing_seconds == 20
    release["a"].set()
    await entered["b"].wait()
    now[0] = 25
    assert (await tracker.get("b", **OWNER)).processing_seconds == 25
    release["b"].set()
    await asyncio.gather(first, second)
    assert (await tracker.get("b", **OWNER)).processing_seconds == 25
    for worker in list(scheduler._workers):
        worker.cancel()
    await asyncio.gather(*scheduler._workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_downstream_work_wait_remains_worker_occupied_time(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("openviking.service.task_store.time.time", lambda: now[0])
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    await _create_work(tracker, "task", "coordinator")
    await tracker.register_work("task", "embedding", "Embedding", **OWNER)

    now[0] = 3
    waiter = asyncio.create_task(tracker.wait_for_work("task", ["embedding"], **OWNER))
    await asyncio.sleep(0.01)
    now[0] = 20
    assert (await tracker.get("task", **OWNER)).processing_seconds == 20
    await tracker.mark_work_done("task", "embedding", **OWNER)
    await waiter
    now[0] = 22
    assert (await tracker.get("task", **OWNER)).processing_seconds == 22
    await tracker.mark_work_done("task", "coordinator", **OWNER)


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_enqueue", [False, True])
async def test_semantic_retry_cooldown_counts_full_worker_occupied_time(
    monkeypatch, custom_enqueue
):
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    now = [0.0]
    monkeypatch.setattr("openviking.service.task_store.time.time", lambda: now[0])
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    await _create_work(tracker, "task")
    message = SimpleNamespace(uri="viking://resources/retry-test", account_id="account")
    enqueued = []

    async def sleep(delay):
        assert delay == 30
        now[0] += delay
        assert (await tracker.get("task", **OWNER)).processing_seconds == 33

    async def enqueue(msg):
        assert msg is message
        now[0] += 2
        enqueued.append(msg)

    queue = SimpleNamespace(enqueue=enqueue)

    async def custom(queue_arg, msg):
        assert queue_arg is queue
        await enqueue(msg)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(SEMANTIC="semantic", get_queue=lambda _: queue),
    )
    processor = SimpleNamespace(
        _account_breaker=lambda _account_id: SimpleNamespace(retry_after=30)
    )
    now[0] = 3
    await SemanticProcessor._reenqueue_semantic_msg(
        processor, message, enqueue=custom if custom_enqueue else None
    )
    assert enqueued == [message]
    assert (await tracker.get("task", **OWNER)).processing_seconds == 35

    await tracker.mark_work_requeued("task", "worker", **OWNER)
    assert (await tracker.get("task", **OWNER)).processing_seconds is None
    now[0] = 50
    await tracker.start_work("task", "worker", **OWNER)
    now[0] = 55
    await tracker.mark_work_done("task", "worker", **OWNER)
    assert (await tracker.get("task", **OWNER)).processing_seconds == 5
