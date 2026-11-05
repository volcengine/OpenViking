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
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.observability.context import get_root_observability_context
from openviking.server.identity import RequestContext, Role
from openviking.session.session import Session
from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor
from openviking_cli.session.user_id import UserIdentifier


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


async def test_process_requeues_deferred_commit_and_resets_root_context(monkeypatch):
    queued = []

    class _QueueManager:
        async def enqueue(self, queue_name, data):
            queued.append((queue_name, data))

    processor = SessionCommitProcessor(
        SimpleNamespace(session=Mock(return_value=_FakeSession({}, processed=False))),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: _QueueManager(),
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    wait = AsyncMock()
    monkeypatch.setattr(
        "openviking.storage.queuefs.session_commit_processor.sleep", wait, raising=False
    )
    message = _make_msg()
    for _ in range(8):
        assert await processor._process(message, ctx) is False
        message = SessionCommitMsg.from_dict(queued[-1][1])
    assert [call.args[0] for call in wait.call_args_list] == [1, 2, 4, 8, 16, 30, 30, 30]
    assert all(name == "SessionCommit" for name, _ in queued)
    processor._session_service.session.return_value._processed = True
    assert await processor._process(message, ctx) is True
    assert len(queued) == wait.await_count == 8
    assert get_root_observability_context() is None


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


async def test_deferred_wait_keeps_delivery_on_shutdown_and_allows_other_sessions(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(_delay):
        entered.set()
        await release.wait()

    monkeypatch.setattr("openviking.storage.queuefs.session_commit_processor.sleep", wait)
    tracker = SimpleNamespace(create=AsyncMock())
    monkeypatch.setattr(
        "openviking.storage.queuefs.session_commit_processor.get_task_tracker", lambda: tracker
    )
    blocked = _FakeSession({}, processed=False)
    ready = _FakeSession({}, processed=True)
    processor = SessionCommitProcessor(
        SimpleNamespace(
            session=lambda ctx, session_id, **kw: blocked if session_id == "sess-1" else ready
        )
    )
    queue = NamedQueue(Mock(), "/queue", "SessionCommit", dequeue_handler=processor)
    queue._ensure_initialized = AsyncMock()
    queue._read_queue_message = AsyncMock(
        return_value={"id": "delivery-1", "data": _make_msg().to_dict()}
    )
    queue.ack = AsyncMock()
    task = asyncio.create_task(queue.dequeue())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        other = _make_msg()
        other.session_id = "other-session"
        assert (
            await processor.on_dequeue({"data": other.to_dict()})
        ).outcome is ProcessOutcome.SUCCESS
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    queue.ack.assert_not_awaited()
