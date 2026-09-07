# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SessionCommitProcessor observability identity binding.

Phase-2 memory extraction runs in this queue worker; its VLM/embedding token
events read identity from the root observability context. These tests assert the
worker binds the committing account/user (so tokens are not attributed to
"__unknown__") and resets the context afterwards.
"""

import asyncio
import concurrent.futures
import json
from unittest.mock import Mock

from openviking.observability.context import get_root_observability_context
from openviking.server.identity import RequestContext, Role
from openviking.session.session import Session
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor
from openviking_cli.session.user_id import UserIdentifier


class _FakeSession:
    def __init__(self, captured: dict, processed: bool = True) -> None:
        self._captured = captured
        self._processed = processed

    async def exists(self) -> bool:
        return True

    async def load(self) -> None:
        return None

    async def resume_queued_commit(self, msg) -> bool:
        root = get_root_observability_context()
        self._captured["account_id"] = root.account_id if root else None
        self._captured["user_id"] = root.user_id if root else None
        return self._processed


class _FakeSessionService:
    def __init__(self, captured: dict, processed: bool = True) -> None:
        self._captured = captured
        self._processed = processed

    def session(self, ctx, session_id, session_uri=None):
        return _FakeSession(self._captured, self._processed)


class _MemoryVikingFS:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def stat(self, uri, ctx=None):
        return {"path": uri}

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content


class _SingleSessionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def session(self, ctx, session_id, session_uri=None):
        return self._session


def _make_msg() -> SessionCommitMsg:
    return SessionCommitMsg(
        task_id="task-1",
        session_id="sess-1",
        session_uri="viking://user/alice/sessions/sess-1",
        archive_uri="viking://user/alice/sessions/sess-1/history/archive_001",
        user={"account_id": "acme", "user_id": "alice"},
    )


async def test_process_binds_committing_identity_to_root_context():
    captured: dict = {}
    processor = SessionCommitProcessor(
        _FakeSessionService(captured),
        asyncio.get_running_loop(),
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    await processor._process(_make_msg(), ctx)

    assert captured["account_id"] == "acme"
    assert captured["user_id"] == "alice"


async def test_process_requeues_deferred_commit_and_resets_root_context(monkeypatch):
    queued = []

    class _QueueManager:
        async def enqueue(self, queue_name, data):
            queued.append((queue_name, data))

    processor = SessionCommitProcessor(
        _FakeSessionService({}, processed=False),
        asyncio.get_running_loop(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: _QueueManager(),
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    await processor._process(_make_msg(), ctx)

    expected = _make_msg().to_dict()
    expected["dependency_wait_count"] = 1
    assert queued == [("SessionCommit", expected)]
    assert get_root_observability_context() is None


async def test_cancelled_queued_commit_writes_terminal_marker_before_success(monkeypatch):
    msg = _make_msg()
    viking_fs = _MemoryVikingFS()
    session = Session(
        viking_fs=viking_fs,
        session_id=msg.session_id,
        session_uri=msg.session_uri,
    )
    processor = SessionCommitProcessor(
        _SingleSessionService(session),
        asyncio.get_running_loop(),
    )
    marker_uri = f"{msg.archive_uri}/.failed.json"
    on_success = Mock(side_effect=lambda: viking_fs.files[marker_uri])
    processor.set_callbacks(on_success, Mock(), Mock())

    def run_on_current_loop(coro, _loop):
        task = asyncio.create_task(coro)
        future: concurrent.futures.Future[None] = concurrent.futures.Future()

        def complete(completed: asyncio.Task) -> None:
            if completed.cancelled():
                future.cancel()
                return
            error = completed.exception()
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(completed.result())

        task.add_done_callback(complete)
        return future

    monkeypatch.setattr(
        "openviking.storage.queuefs.session_commit_processor.asyncio.run_coroutine_threadsafe",
        run_on_current_loop,
    )

    await processor.on_cancelled({"data": json.dumps(msg.to_dict())})

    marker = json.loads(viking_fs.files[marker_uri])
    assert marker["stage"] == "cancelled"
    assert marker["error"] == "session commit cancelled"
    on_success.assert_called_once_with()


async def test_dependency_blocked_commit_is_paced_and_counts_its_waits(monkeypatch):
    """A commit blocked behind its predecessor must not spin.

    Without a delay the consumer dequeues the blocked message, finds it still
    blocked and re-enqueues it as fast as it can turn, which is how a queue of
    33 waiters reached a requeue count in the hundreds of thousands while its
    depth never dropped. Assert both halves of the pacing: the worker actually
    waits, and the wait it already served rides along on the message so the
    next attempt waits longer instead of restarting from zero.
    """
    queued: list = []
    slept: list = []

    class _QueueManager:
        async def enqueue(self, queue_name, data):
            queued.append(data)

    async def _record_sleep(delay):
        slept.append(delay)

    processor = SessionCommitProcessor(
        _FakeSessionService({}, processed=False),
        asyncio.get_running_loop(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: _QueueManager(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.session_commit_processor.asyncio.sleep",
        _record_sleep,
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    msg = _make_msg()
    for _ in range(3):
        await processor._process(msg, ctx)
        msg = SessionCommitMsg.from_dict(queued[-1])

    assert [item["dependency_wait_count"] for item in queued] == [1, 2, 3]
    assert all(delay > 0 for delay in slept)
    assert slept == sorted(slept)
    assert slept[0] < slept[-1]


def test_dependency_wait_delay_grows_then_holds_at_the_cap():
    """The wait doubles, then stops growing.

    A predecessor that runs for an hour must not push the commits behind it into
    an hour-long poll interval, so the delay is capped rather than unbounded.
    """
    delays = [SessionCommitProcessor._dependency_wait_delay(n) for n in range(0, 14)]

    assert delays[0] == SessionCommitProcessor._DEPENDENCY_WAIT_BASE_DELAY
    assert delays == sorted(delays)
    assert max(delays) == SessionCommitProcessor._DEPENDENCY_WAIT_MAX_DELAY
    assert delays[-1] == delays[-2] == SessionCommitProcessor._DEPENDENCY_WAIT_MAX_DELAY
    # A negative or absent counter from an older producer must not underflow.
    assert (
        SessionCommitProcessor._dependency_wait_delay(-5)
        == SessionCommitProcessor._DEPENDENCY_WAIT_BASE_DELAY
    )
