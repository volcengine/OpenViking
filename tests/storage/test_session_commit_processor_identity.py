# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SessionCommitProcessor observability identity binding.

Phase-2 memory extraction runs in this queue worker; its VLM/embedding token
events read identity from the root observability context. These tests assert the
worker binds the committing account/user (so tokens are not attributed to
"__unknown__") and resets the context afterwards.
"""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.observability.context import get_root_observability_context
from openviking.service.task_domain import WorkState
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.service.task_work_index import bind_task_context
from openviking.session.memory.utils.streaming_batcher import (
    StreamingBatcher,
    StreamingBatcherConfig,
)
from openviking.session.session import Session
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor
from tests.test_task_tracker import _FakeAgfs


class _FakeSession:
    def __init__(self, captured: dict, processed: bool = True) -> None:
        self._captured = captured
        self._processed = processed

    async def exists(self) -> bool:
        return True

    async def resume_queued_commit(self, msg) -> bool:
        root = get_root_observability_context()
        self._captured["account_id"] = root.account_id if root else None
        self._captured["user_id"] = root.user_id if root else None
        return self._processed


class _MemoryVikingFS:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def stat(self, uri, ctx=None, skip_count=False):
        return {"path": uri}

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content


def _make_msg() -> SessionCommitMsg:
    return SessionCommitMsg(
        task_id="task-1",
        session_id="sess-1",
        session_uri="viking://user/alice/sessions/sess-1",
        archive_uri="viking://user/alice/sessions/sess-1/history/archive_001",
        user={"account_id": "acme", "user_id": "alice"},
    )


async def test_process_binds_committing_identity_to_root_context(monkeypatch):
    captured: dict = {}
    session = _FakeSession(captured)
    processor = SessionCommitProcessor(SimpleNamespace(session=Mock(return_value=session)))
    tracker = SimpleNamespace(create=AsyncMock(side_effect=OSError("store unavailable")))
    monkeypatch.setattr(
        "openviking.storage.queuefs.session_commit_processor.get_task_tracker", lambda: tracker
    )
    message = {"data": _make_msg().to_dict()}
    with pytest.raises(OSError, match="store unavailable"):
        await processor.on_dequeue(message)
    tracker.create.side_effect = None
    original_resume = session.resume_queued_commit
    session.resume_queued_commit = AsyncMock(side_effect=ValueError("invalid archive message"))
    result = await processor.on_dequeue(message)
    assert result.outcome is ProcessOutcome.FAILED
    assert result.error == "invalid archive message"
    session.resume_queued_commit = original_resume
    assert (await processor.on_dequeue(message)).outcome is ProcessOutcome.SUCCESS
    assert captured == {"account_id": "acme", "user_id": "alice"}
    assert get_root_observability_context() is None


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_deferred_commit_retries_original_and_allows_predecessor_to_run(
    monkeypatch, tmp_path, backend
):
    import ragfs_python

    class OrderedSession(_FakeSession):
        async def resume_queued_commit(self, msg):
            if msg.archive_uri.endswith("archive_001"):
                self._processed = True
            return await super().resume_queued_commit(msg)

    captured = {}
    session = OrderedSession(captured, processed=False)
    processor = SessionCommitProcessor(SimpleNamespace(session=Mock(return_value=session)))
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    client = ragfs_python.RAGFSBindingClient()
    client.mount(
        "queuefs", "/queue", {"backend": backend, "db_path": str(tmp_path / "queue.sqlite")}
    )
    queue = NamedQueue(
        client,
        "/queue",
        "SessionCommit",
        dequeue_handler=processor,
        middlewares=[TaskWorkQueueMiddleware()],
    )
    owner = {"account_id": "acme", "user_id": "alice"}
    msg = _make_msg()
    predecessor = replace(msg, task_id="task-0")
    msg.archive_uri = msg.archive_uri.replace("archive_001", "archive_002")
    try:
        await tracker.create("session_commit", task_id=msg.task_id, **owner)
        with bind_task_context(msg.task_id, "acme", "alice"):
            receipt = await queue.enqueue(msg.to_dict(), group_key=msg.queue_group_key)
        [original] = await queue.snapshot()
        for count in range(1, 5):
            await queue.dequeue()
            assert await queue.snapshot() == [original]
            status = await queue.get_status()
            assert (status.pending, status.in_progress, status.requeue_count) == (1, 0, count)
            assert (
                await tracker.get_work_state(msg.task_id, receipt.work_id, **owner)
                is WorkState.PENDING
            )
            summary = await tracker.get_work_summary(msg.task_id, **owner)
            assert summary.queues["SessionCommit"].requeue_count == count
            assert get_root_observability_context() is None

        await tracker.create("session_commit", task_id=predecessor.task_id, **owner)
        with bind_task_context(predecessor.task_id, "acme", "alice"):
            earlier = await queue.enqueue(
                predecessor.to_dict(), group_key=predecessor.queue_group_key
            )
        await queue.dequeue()  # The deferred message moves behind its predecessor.
        pending = await queue.snapshot()
        assert {message["id"] for message in pending} == {earlier.message_id, original["id"]}
        await queue.dequeue()  # The predecessor can complete on the same group.
        assert (
            await tracker.get_work_state(predecessor.task_id, earlier.work_id, **owner)
            is WorkState.DONE
        )
        assert await queue.snapshot() == [original]
        await queue.dequeue()
        assert (await queue.get_status()).is_complete
        assert await tracker.get_work_state(msg.task_id, receipt.work_id, **owner) is WorkState.DONE
        summary = await tracker.get_work_summary(msg.task_id, **owner)
        assert summary.queues["SessionCommit"].requeue_count == 5
        assert captured == owner
        assert get_root_observability_context() is None
    finally:
        client.close()


def test_memory_group_key_shares_users_across_sessions_and_isolates_accounts():
    first = _make_msg()
    other_session = _make_msg()
    other_session.session_id = "sess-2"
    assert first.queue_group_key == other_session.queue_group_key
    other_session.user = {"account_id": "acme", "user_id": "bob"}
    assert first.queue_group_key != other_session.queue_group_key
    other_session.user = {"account_id": "other", "user_id": "alice"}
    assert first.queue_group_key != other_session.queue_group_key
    assert "queue_group_key" not in first.to_dict()


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("fail", [False, True])
async def test_native_memory_queue_waits_for_shared_batch_before_ack(monkeypatch, cancel, fail):
    import ragfs_python

    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    batches = []

    async def process_batch(items, reason):
        batches.append(items)
        started.set()
        await release.wait()
        finished.set()
        if fail:
            raise OSError("memory write failed")
        return True

    batcher = StreamingBatcher(
        name="memory-queue-test",
        process_batch=process_batch,
        config=StreamingBatcherConfig(max_items_per_batch=2, max_wait_seconds=60),
    )

    class BatchSession(_FakeSession):
        async def resume_queued_commit(self, msg):
            return await batcher.submit(msg.session_id)

    monkeypatch.setattr(
        "openviking.storage.queuefs.session_commit_processor.get_task_tracker",
        lambda: SimpleNamespace(create=AsyncMock()),
    )
    processor = SessionCommitProcessor(SimpleNamespace(session=Mock(return_value=BatchSession({}))))
    client = ragfs_python.RAGFSBindingClient()
    client.mount("queuefs", "/queue", {"backend": "memory"})
    queue = NamedQueue(client, "/queue", "SessionCommit", dequeue_handler=processor)
    tasks = []
    first = _make_msg()
    second = replace(first, task_id="task-2", session_id="sess-2")
    try:
        await queue.enqueue(first.to_dict(), group_key=first.queue_group_key)
        await queue.enqueue(second.to_dict(), group_key=second.queue_group_key)
        tasks = [asyncio.create_task(queue.dequeue()) for _ in range(2)]
        await asyncio.wait_for(started.wait(), 1)
        status = await queue.get_status()
        assert (status.pending, status.in_progress) == (0, 2)
        assert sorted(batches[0]) == ["sess-1", "sess-2"]
        if cancel:
            tasks[0].cancel()
            await asyncio.sleep(0)
            tasks[0].cancel()
            await asyncio.sleep(0)
        assert not any(task.done() for task in tasks)
        assert not finished.is_set()
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
        assert finished.is_set()
        status = await queue.get_status()
        assert status.pending == 0
        assert status.in_progress == int(cancel)
        if cancel:
            assert isinstance(results[0], asyncio.CancelledError)
            [unacked] = await queue.snapshot()
            await queue.ack(unacked["id"], unacked)
        assert (await queue.get_status()).is_complete
        assert queue._error_count == (2 - int(cancel) if fail else 0)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await batcher.close()
        client.close()


async def test_cancelled_queued_commit_writes_terminal_marker_before_returning():
    msg = _make_msg()
    viking_fs = _MemoryVikingFS()
    session = Session(
        viking_fs=viking_fs,
        session_id=msg.session_id,
        session_uri=msg.session_uri,
    )
    processor = SessionCommitProcessor(
        SimpleNamespace(session=Mock(return_value=session)),
    )
    marker_uri = f"{msg.archive_uri}/.failed.json"

    result = await processor.on_cancelled({"data": json.dumps(msg.to_dict())})

    assert result.outcome is ProcessOutcome.CANCELLED
    marker = json.loads(viking_fs.files[marker_uri])
    assert marker["stage"] == "cancelled"
    assert marker["error"] == "session commit cancelled"
