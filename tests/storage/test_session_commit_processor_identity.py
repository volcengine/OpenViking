# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for SessionCommitProcessor observability identity binding.

Phase-2 memory extraction runs in this queue worker; its VLM/embedding token
events read identity from the root observability context. These tests assert the
worker binds the committing account/user (so tokens are not attributed to
"__unknown__") and resets the context afterwards.
"""

import json

from openviking.observability.context import get_root_observability_context
from openviking.server.identity import RequestContext, Role
from openviking.session.session import Session
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

    async def stat(self, uri, ctx=None, skip_count=False):
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
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    result = await processor._process(_make_msg(), ctx)

    assert result is True
    assert captured["account_id"] == "acme"
    assert captured["user_id"] == "alice"


async def test_process_requeues_deferred_commit_and_resets_root_context(monkeypatch):
    queued = []

    class _QueueManager:
        async def enqueue(self, queue_name, data):
            queued.append((queue_name, data))

    processor = SessionCommitProcessor(
        _FakeSessionService({}, processed=False),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: _QueueManager(),
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    result = await processor._process(_make_msg(), ctx)

    assert result is False
    assert queued == [("SessionCommit", _make_msg().to_dict())]
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
        _SingleSessionService(session),
    )
    marker_uri = f"{msg.archive_uri}/.failed.json"

    result = await processor.on_cancelled({"data": json.dumps(msg.to_dict())})

    assert result.outcome is ProcessOutcome.CANCELLED
    assert result.value is None
    assert result.error is None
    marker = json.loads(viking_fs.files[marker_uri])
    assert marker["stage"] == "cancelled"
    assert marker["error"] == "session commit cancelled"


class _CrashingLoadSession:
    """Session whose load() raises, e.g. a torn read of live messages.jsonl."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def exists(self) -> bool:
        return True

    async def load(self) -> None:
        raise self._error

    async def resume_queued_commit(self, msg) -> bool:
        raise AssertionError("resume_queued_commit must not run after a failed load")


class _CrashingLoadSessionService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def session(self, ctx, session_id, session_uri=None):
        return _CrashingLoadSession(self._error)


class _FakeTaskTracker:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.failed: list[dict] = []

    async def create(
        self,
        task_type,
        resource_id=None,
        *,
        account_id=None,
        user_id=None,
        task_id=None,
        meta=None,
        auth=None,
    ):
        self.created.append(
            {
                "task_type": task_type,
                "resource_id": resource_id,
                "account_id": account_id,
                "user_id": user_id,
                "task_id": task_id,
            }
        )

    async def fail(
        self,
        task_id,
        error,
        account_id=None,
        user_id=None,
        *,
        result=None,
    ):
        self.failed.append(
            {
                "task_id": task_id,
                "error": error,
                "account_id": account_id,
                "user_id": user_id,
            }
        )


def _install_fake_tracker(monkeypatch) -> _FakeTaskTracker:
    tracker = _FakeTaskTracker()
    monkeypatch.setattr(
        "openviking.storage.queuefs.session_commit_processor.get_task_tracker",
        lambda: tracker,
    )
    return tracker


async def test_on_dequeue_settles_worker_crash_as_failed_delivery(monkeypatch):
    """A crash inside _process must settle the delivery instead of escaping.

    Before the guard, a json.JSONDecodeError from Session.load() propagated out
    of on_dequeue; the queue layer swallowed it and the task stayed pending
    forever (reported as "Phase 2 timed out" by clients).
    """
    tracker = _install_fake_tracker(monkeypatch)
    crash = json.JSONDecodeError(
        "Unterminated string starting at: line 1 column 59183 (char 59182)",
        "x" * 60000,
        59182,
    )
    processor = SessionCommitProcessor(_CrashingLoadSessionService(crash))
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)

    result = await processor.on_dequeue({"data": json.dumps(_make_msg().to_dict())})

    assert result.outcome is ProcessOutcome.FAILED
    assert result.error is not None
    assert "Unterminated string starting at: line 1 column 59183" in result.error
    assert tracker.created == [
        {
            "task_type": "session_commit",
            "resource_id": "sess-1",
            "account_id": ctx.account_id,
            "user_id": "alice",
            "task_id": "task-1",
        }
    ]
    assert len(tracker.failed) == 1
    assert tracker.failed[0]["task_id"] == "task-1"
    assert tracker.failed[0]["error"] == result.error
    assert tracker.failed[0]["account_id"] == ctx.account_id
    assert tracker.failed[0]["user_id"] == "alice"
    assert get_root_observability_context() is None


async def test_on_dequeue_worker_crash_reuses_phase1_task_id(monkeypatch):
    """Repeated crashes create against the same explicit task_id.

    tracker.create is idempotent for an explicit task_id owned by the same
    user, so a Phase-1-created record is reused instead of duplicated.
    """
    tracker = _install_fake_tracker(monkeypatch)
    crash = json.JSONDecodeError("bad", "doc", 0)
    processor = SessionCommitProcessor(_CrashingLoadSessionService(crash))

    await processor.on_dequeue({"data": json.dumps(_make_msg().to_dict())})
    await processor.on_dequeue({"data": json.dumps(_make_msg().to_dict())})

    assert [entry["task_id"] for entry in tracker.created] == ["task-1", "task-1"]
    assert [entry["task_id"] for entry in tracker.failed] == ["task-1", "task-1"]


async def test_on_dequeue_success_and_requeue_paths_do_not_touch_tracker(monkeypatch):
    tracker = _install_fake_tracker(monkeypatch)

    processor = SessionCommitProcessor(_FakeSessionService({}))
    result = await processor.on_dequeue({"data": json.dumps(_make_msg().to_dict())})

    assert result.outcome is ProcessOutcome.SUCCESS
    assert tracker.created == []
    assert tracker.failed == []

    queued = []

    class _QueueManager:
        async def enqueue(self, queue_name, data):
            queued.append((queue_name, data))

    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: _QueueManager(),
    )
    requeue_processor = SessionCommitProcessor(_FakeSessionService({}, processed=False))
    requeue_result = await requeue_processor.on_dequeue({"data": json.dumps(_make_msg().to_dict())})

    assert requeue_result.outcome is ProcessOutcome.REQUEUED
    assert tracker.created == []
    assert tracker.failed == []


async def test_on_dequeue_parse_failure_does_not_touch_tracker(monkeypatch):
    tracker = _install_fake_tracker(monkeypatch)
    processor = SessionCommitProcessor(_FakeSessionService({}))

    result = await processor.on_dequeue({"data": "not json"})

    assert result.outcome is ProcessOutcome.FAILED
    assert result.error is not None
    assert tracker.created == []
    assert tracker.failed == []
