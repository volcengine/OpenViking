# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Processing time measures the wall-clock union of worker-owned execution."""

import asyncio
from types import SimpleNamespace

import pytest

from openviking.service.task_work_index import (
    QueueTaskMetadata,
    TaskWorkIndex,
    bind_task_context,
)
from openviking.storage.queuefs.semantic_executor import (
    SemanticTreeExecutor,
    SemanticTreeScheduler,
)


def test_work_timestamps_union_worker_occupancy_and_exclude_queue_time(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("openviking.service.task_work_index.time.time", lambda: now[0])
    index = TaskWorkIndex()
    index.init_processing("task")
    first = QueueTaskMetadata("task", "first")
    second = QueueTaskMetadata("task", "second")
    assert index.register("Semantic", first)
    assert index.processing_seconds("task") is None

    now[0] = 10  # Initial queue wait.
    first_worker = object()
    index.register_active("task", first_worker, work_id=first.work_id)
    now[0] = 12
    assert index.register("Embedding", second)
    second_worker = object()
    index.register_active("task", second_worker, work_id=second.work_id)
    now[0] = 15
    index.unregister_active("task", first_worker)
    now[0] = 18
    index.unregister_active("task", second_worker)
    assert index.processing_seconds("task") == 8
    now[0] = 40  # No worker: waiting for another queue.
    assert index.processing_seconds("task") == 8
    third_worker = object()
    index.register_active("task", third_worker)
    now[0] = 43
    assert index.processing_seconds("task") == 11
    index.unregister_active("task", third_worker)
    now[0] = 100
    assert index.processing_seconds("task") == 11


@pytest.mark.asyncio
async def test_saturated_dag_scheduler_counts_claimed_handler_time(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(
        "openviking.service.task_work_index.time",
        SimpleNamespace(time=lambda: now[0]),
    )
    index = TaskWorkIndex()
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
        index.init_processing(name)
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
        worker = asyncio.current_task()
        assert worker is not None
        index.register_active(name, worker)
        try:
            await executors[name].run("viking://resources/" + name)
        finally:
            index.unregister_active(name, worker)

    first = asyncio.create_task(coordinate("a"))
    await entered["a"].wait()
    second = asyncio.create_task(coordinate("b"))
    await queued.wait()
    now[0] = 20
    assert index.processing_seconds("a") == 20
    # B's QueueFS handler already owns a worker while its internal DAG node
    # waits for the shared scheduler.
    assert index.processing_seconds("b") == 20
    release["a"].set()
    await entered["b"].wait()
    now[0] = 25
    assert index.processing_seconds("b") == 25
    release["b"].set()
    await asyncio.gather(first, second)
    assert index.processing_seconds("b") == 25
    for worker in list(scheduler._workers):
        worker.cancel()
    await asyncio.gather(*scheduler._workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_downstream_work_wait_remains_worker_occupied_time(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(
        "openviking.service.task_work_index.time",
        SimpleNamespace(time=lambda: now[0]),
    )
    index = TaskWorkIndex()
    index.init_processing("task")
    metadata = QueueTaskMetadata("task", "embedding")
    assert index.register("Embedding", metadata)
    worker = asyncio.current_task()
    assert worker is not None
    index.register_active("task", worker)
    try:
        now[0] = 3
        waiter = asyncio.create_task(
            index.wait_for_work("task", ["embedding"], poll_interval=0.001)
        )
        await asyncio.sleep(0.01)
        now[0] = 20
        assert index.processing_seconds("task") == 20
        index.record_result(metadata, "completed")
        await waiter
        now[0] = 22
        assert index.processing_seconds("task") == 22
    finally:
        index.unregister_active("task", worker)


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_enqueue", [False, True])
async def test_semantic_retry_cooldown_counts_full_worker_occupied_time(
    monkeypatch, custom_enqueue
):
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    now = [0.0]
    monkeypatch.setattr(
        "openviking.service.task_work_index.time",
        SimpleNamespace(time=lambda: now[0]),
    )
    index = TaskWorkIndex()
    index.init_processing("task")
    message = SimpleNamespace(uri="viking://resources/retry-test", account_id="account")
    enqueued = []

    async def sleep(delay):
        assert delay == 30
        now[0] += delay
        assert index.processing_seconds("task") == 33

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
    worker = asyncio.current_task()
    index.register_active("task", worker)
    try:
        now[0] = 3
        await SemanticProcessor._reenqueue_semantic_msg(
            processor, message, enqueue=custom if custom_enqueue else None
        )
    finally:
        index.unregister_active("task", worker)

    assert enqueued == [message]
    assert index.processing_seconds("task") == 35
