# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Processing time excludes idle gaps and unions overlapping workers."""

import asyncio
from types import SimpleNamespace

import pytest

from openviking.service.task_processing_time import ProcessingClock
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import TaskWorkIndex, bind_task_context
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor, SemanticNodeScheduler
from openviking.telemetry.request_wait_tracker import RequestWaitTracker


def test_processing_clock_unions_workers_and_excludes_idle_time(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("openviking.service.task_processing_time.time.monotonic", lambda: now[0])
    clock = ProcessingClock()
    assert clock.seconds() is None
    now[0] = 10  # Initial queue wait.
    clock.enter("parse")
    now[0] = 12
    clock.enter("embedding")
    now[0] = 15
    clock.leave("parse")
    now[0] = 18
    clock.leave("embedding")
    assert clock.seconds() == 8
    now[0] = 40  # No worker: waiting for another queue.
    assert clock.seconds() == 8
    clock.enter("semantic")
    now[0] = 43
    assert clock.seconds() == 11
    clock.leave("semantic")
    now[0] = 100
    assert clock.seconds() == 11


@pytest.mark.asyncio
async def test_saturated_dag_scheduler_counts_only_dispatched_nodes(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(
        "openviking.service.task_processing_time.time",
        SimpleNamespace(monotonic=lambda: now[0]),
    )
    index = TaskWorkIndex()
    scheduler = SemanticNodeScheduler(max_workers=1)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_semantic_node_scheduler",
        lambda _: scheduler,
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: None)
    entered = {name: asyncio.Event() for name in ["a", "b"]}
    release = {name: asyncio.Event() for name in ["a", "b"]}
    queued = asyncio.Event()
    executors = {}
    for name in ["a", "b"]:
        index.init_processing(name)
        with bind_task_context(name, "account", "user"):
            executor = SemanticDagExecutor(None, "resource", 1, None)
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
        with index.measure_processing(name):
            await executors[name].run("viking://resources/" + name)

    first = asyncio.create_task(coordinate("a"))
    await entered["a"].wait()
    second = asyncio.create_task(coordinate("b"))
    await queued.wait()
    now[0] = 20
    assert index.processing_seconds("a") == 20
    assert index.processing_seconds("b") == 0  # Still queued behind A.
    release["a"].set()
    await entered["b"].wait()
    now[0] = 25
    assert index.processing_seconds("b") == 5
    release["b"].set()
    await asyncio.gather(first, second)
    assert index.processing_seconds("b") == 5
    for worker in list(scheduler._workers):
        worker.cancel()
    await asyncio.gather(*scheduler._workers, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["wait_for_request", "wait_for_embeddings"])
async def test_downstream_wait_pauses_inherited_owner_in_shielded_child(monkeypatch, method):
    now = [0.0]
    monkeypatch.setattr(
        "openviking.service.task_processing_time.time",
        SimpleNamespace(monotonic=lambda: now[0]),
    )
    index = TaskWorkIndex()
    index.init_processing("task")
    tracker = RequestWaitTracker()
    request = "processing-time-" + method
    tracker.register_request(request)
    tracker.register_embedding_root(request, "embedding")
    with index.measure_processing("task"):
        now[0] = 3
        waiter = asyncio.create_task(
            run_to_completion(lambda: getattr(tracker, method)(request, poll_interval=0.001))
        )
        # Let run_to_completion and its shielded child enter the wait loop.
        await asyncio.sleep(0.01)
        now[0] = 20
        assert index.processing_seconds("task") == 3
        tracker.mark_embedding_done(request, "embedding")
        await waiter
        now[0] = 22
        assert index.processing_seconds("task") == 5
    tracker.cleanup(request)


@pytest.mark.asyncio
async def test_nested_wait_and_cancellation_restore_processing_owner(monkeypatch):
    from openviking.service.task_processing_time import pause_task_processing, processing_owner

    now = [0.0]
    monkeypatch.setattr(
        "openviking.service.task_processing_time.time",
        SimpleNamespace(monotonic=lambda: now[0]),
    )
    index = TaskWorkIndex()
    index.init_processing("task")
    entered = asyncio.Event()

    async def work():
        with index.measure_processing("task"):
            now[0] = 2
            try:
                with pause_task_processing():
                    with pause_task_processing():
                        entered.set()
                        await asyncio.Event().wait()
            except asyncio.CancelledError:
                now[0] += 3  # Cancellation cleanup resumes processing.
        assert processing_owner.get() is None

    task = asyncio.create_task(work())
    await entered.wait()
    now[0] = 20
    assert index.processing_seconds("task") == 2
    task.cancel()
    await task
    assert index.processing_seconds("task") == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_enqueue", [False, True])
async def test_semantic_retry_cooldown_excludes_wait_but_counts_enqueue(monkeypatch, custom_enqueue):
    from openviking.storage.queuefs.semantic_processor import SemanticProcessor

    now = [0.0]
    monkeypatch.setattr(
        "openviking.service.task_processing_time.time",
        SimpleNamespace(monotonic=lambda: now[0]),
    )
    index = TaskWorkIndex()
    index.init_processing("task")
    message = SimpleNamespace(uri="viking://resources/retry-test")
    enqueued = []

    async def sleep(delay):
        assert delay == 30
        now[0] += delay
        assert index.processing_seconds("task") == 3

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
    processor = SimpleNamespace(_circuit_breaker=SimpleNamespace(retry_after=30))
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
    assert index.processing_seconds("task") == 5
