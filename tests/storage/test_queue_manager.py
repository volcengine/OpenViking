# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused tests for QueueManager concurrency selection."""

import json
import os
import subprocess
import sys
import threading
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.service.task_work_index import bind_task_context
from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.storage.queuefs.queue_middleware import QueueMiddleware


def test_queuefs_package_imports_in_a_clean_process(tmp_path) -> None:
    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(tmp_path / "missing-ov.conf")

    subprocess.run(
        [sys.executable, "-c", "from openviking.storage.queuefs import QueueManager"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_queue_concurrency_uses_separate_configured_values() -> None:
    manager = QueueManager(
        agfs=object(),
        max_concurrent_external_parse=9,
        max_concurrent_add_resource=7,
        max_concurrent_session_commit=5,
    )

    assert manager._max_concurrent_for_queue(manager.EXTERNAL_PARSE) == 9
    assert manager._max_concurrent_for_queue(manager.ADD_RESOURCE) == 7
    assert manager._max_concurrent_for_queue(manager.SESSION_COMMIT) == 5


async def test_status_waits_for_processing_messages_from_other_workers(monkeypatch) -> None:
    client = AsyncMock()

    async def read(path: str):
        if path.endswith("/status"):
            return b'{"pending":0,"processing":1}'
        raise AssertionError(f"unexpected read: {path}")

    client.read.side_effect = read
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient",
        lambda _: client,
    )

    status = await NamedQueue(object(), "/queue", "Test").get_status()

    assert status.pending == 0
    assert status.in_progress == 1
    assert not status.is_complete


@pytest.fixture
def transport(monkeypatch):
    client = AsyncMock(spec=AsyncAGFSClient)
    client.write.return_value = "message-1"
    client.read.return_value = b"0"
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda _client: client
    )
    return client


async def test_constructor_middlewares_apply_to_all_queues(transport):
    calls = []

    class Recorder(QueueMiddleware):
        async def enqueue(self, ctx, call_next):
            calls.append(ctx.queue)
            return await call_next(ctx)

    middlewares = [Recorder()]
    manager = QueueManager(object(), middlewares=middlewares)
    middlewares.clear()
    existing = manager.get_queue("existing", allow_create=True)
    with bind_task_context("task", "account", "user"):
        await existing.enqueue({})
        middlewares.append(Recorder())
        future = manager.get_queue("future", allow_create=True)
        await future.enqueue({})
    assert calls == ["existing", "future"]
    assert manager._task_work_index.has_work("task")
    assert transport.write.await_count == 2


async def test_init_queue_manager_forwards_constructor_middlewares(transport, monkeypatch):
    from openviking.storage.queuefs import queue_manager as queue_module

    calls = []

    class Recorder(QueueMiddleware):
        async def enqueue(self, ctx, call_next):
            calls.append(ctx.queue)
            return await call_next(ctx)

    monkeypatch.setattr(queue_module, "_instance", None)
    manager = queue_module.init_queue_manager(object(), middlewares=[Recorder()])
    assert queue_module.get_queue_manager() is manager
    await manager.get_queue("Test", allow_create=True).enqueue({})
    assert calls == ["Test"]
    transport.write.assert_awaited_once_with("/queue/Test/enqueue", b"{}")


@pytest.mark.parametrize("fail", [False, True])
async def test_concurrent_worker_uses_process_and_ack_middleware(transport, fail):
    events = []
    stop = threading.Event()

    class Recorder(QueueMiddleware):
        async def process(self, ctx, call_next):
            events.append("process")
            return await call_next(ctx)

        async def ack(self, ctx, call_next):
            events.append("ack")
            return await call_next(ctx)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            stop.set()
            if fail:
                raise RuntimeError("worker failure")
            return ProcessResult.success(data)

    manager = QueueManager(object(), middlewares=[Recorder()])
    queue = manager.get_queue("Test", dequeue_handler=Handler(), allow_create=True)
    reads = [b'{"id":"message-1","data":"{}"}']

    async def read(path):
        return reads.pop(0) if reads else b"{}"

    transport.read.side_effect = read
    await manager._worker_async_concurrent(queue, stop, 2)
    assert queue._processed == (not fail)
    assert queue._error_count == fail
    assert events == (["process"] if fail else ["process", "ack"])
    if fail:
        transport.write.assert_not_awaited()
    else:
        transport.write.assert_awaited_once_with("/queue/Test/ack", b"message-1")


async def test_bootstrap_restores_legacy_messages_into_middleware_index(transport):
    manager = QueueManager(object())
    queue = manager.get_queue("Test", allow_create=True)
    message = {
        "id": "legacy-message",
        "data": json.dumps({"task_id": "task", "account_id": "account", "user_id": "user"}),
    }
    transport.read.return_value = json.dumps([message]).encode()
    attached = []

    class Tracker:
        def attach_work_index(self, index):
            attached.append(index)
            assert index.has_work("task")

        async def restore_work_tasks(self, owners):
            assert owners == {"task": ("account", "user")}
            return ["restored"]

    assert await manager.prepare_task_tracking(Tracker()) == ["restored"]
    assert attached == [manager._task_work_index]
    await queue.ack("legacy-message", message)
    assert not attached[0].has_work("task")
    transport.write.assert_awaited_once_with("/queue/Test/ack", b"legacy-message")
