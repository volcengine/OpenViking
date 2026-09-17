# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for retrying failed session commit (Phase 2) archives."""

import json
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.session.session import Session
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg


class _TaskStore:
    def __init__(self):
        self.tasks = {}

    async def create(self, task):
        self.tasks[task.task_id] = task

    async def update(self, task):
        self.tasks[task.task_id] = task

    async def get(self, task_id, *, account_id=None, user_id=None):
        return None

    async def list(self, account_id, *, user_id=None):
        return []

    async def delete(self, task_id, *, account_id, user_id=None):
        self.tasks.pop(task_id, None)


class _MemoryVikingFS:
    def __init__(self, files):
        self.files = files
        self._async_agfs = AsyncMock()

    def _uri_to_path(self, uri, ctx=None):
        return "/local/session-1"

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content

    async def exists(self, uri, ctx=None):
        return uri in self.files

    async def rm(self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True):
        self.files.pop(uri, None)

    async def ls(self, uri, ctx=None, **kwargs):
        prefix = uri.rstrip("/") + "/"
        names = []
        for path in self.files:
            if path.startswith(prefix):
                remainder = path[len(prefix) :]
                head = remainder.split("/", 1)[0]
                if head and head not in names:
                    names.append(head)
        return [{"name": name, "isDir": True} for name in names]


def _write_session_scaffold(files, session_uri, archive_uri, *, failed_stage="memory_extraction"):
    """Build the minimal on-disk state of one failed Phase 2 archive."""
    archived = Message(id="archived", role="user", parts=[TextPart("old")])
    retained = Message(id="retained", role="assistant", parts=[TextPart("new")])
    original_task_id = "task-old"
    queue_message = SessionCommitMsg(
        task_id=original_task_id,
        session_id="session-1",
        session_uri=session_uri,
        archive_uri=archive_uri,
        user={"account_id": "default", "user_id": "default"},
        memory_policy={"memory_types": []},
    )
    files[f"{session_uri}/messages.jsonl"] = f"{retained.to_jsonl()}\n"
    files[f"{session_uri}/.meta.json"] = json.dumps(
        {"session_id": "session-1", "message_count": 1, "commit_count": 1}
    )
    files[f"{archive_uri}/messages.jsonl"] = f"{archived.to_jsonl()}\n"
    files[f"{archive_uri}/.failed.json"] = json.dumps(
        {
            "stage": failed_stage,
            "error": "APITimeoutError",
            "failed_at": "2026-09-11T20:47:40",
            "skipped": True,
            "completed_memory_steps": {},
        }
    )
    files[f"{archive_uri}/.meta.json"] = json.dumps(
        {"phase1": {"status": "ready", "queue_message": queue_message.to_dict()}}
    )
    return queue_message


def _new_session(viking_fs, session_uri="viking://user/default/sessions/session-1"):
    return Session(viking_fs=viking_fs, session_id="session-1", session_uri=session_uri)


class _QueueManagerStub:
    SESSION_COMMIT = "SessionCommit"

    def __init__(self):
        self.enqueued = []

    async def enqueue(self, queue_name, payload):
        self.enqueued.append((queue_name, payload))


@pytest.fixture
def queue_stub(monkeypatch):
    stub = _QueueManagerStub()

    def _fake_get_queue_manager():
        return stub

    import openviking.storage.queuefs as queuefs_pkg

    monkeypatch.setattr(queuefs_pkg, "get_queue_manager", _fake_get_queue_manager)
    monkeypatch.setattr(queuefs_pkg, "QueueManager", stub)
    return stub


@pytest.mark.asyncio
async def test_retry_failed_commit_reenqueues_with_fresh_task_id(queue_stub, monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    files = {}
    _write_session_scaffold(files, session_uri, archive_uri)
    viking_fs = _MemoryVikingFS(files)
    session = _new_session(viking_fs)
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)
    monkeypatch.setattr(session, "_run_memory_extraction", AsyncMock())

    try:
        result = await session.retry_failed_commit()
        assert len(result["retried"]) == 1
        new_task_id = result["retried"][0]["task_id"]
        assert new_task_id != "task-old"
        assert result["retried"][0]["archive_uri"] == archive_uri
        assert result["skipped"] == []
        assert result["failed"] == []

        # Terminal marker cleared, queue message re-enqueued with fresh ID.
        assert f"{archive_uri}/.failed.json" not in viking_fs.files
        assert len(queue_stub.enqueued) == 1
        queue_name, payload = queue_stub.enqueued[0]
        assert queue_name == "SessionCommit"
        assert payload["task_id"] == new_task_id
        assert payload["session_id"] == "session-1"
        assert payload["archive_uri"] == archive_uri

        # A new tracker task exists for polling.
        task = await tracker.get(new_task_id)
        assert task is not None
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_retry_skips_completed_and_pending_archives(queue_stub, monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    failed_uri = f"{session_uri}/history/archive_001"
    done_uri = f"{session_uri}/history/archive_002"
    pending_uri = f"{session_uri}/history/archive_003"
    files = {}
    _write_session_scaffold(files, session_uri, failed_uri)
    done = Message(id="done", role="user", parts=[TextPart("done")])
    pending = Message(id="pending", role="user", parts=[TextPart("pending")])
    files[f"{done_uri}/messages.jsonl"] = f"{done.to_jsonl()}\n"
    files[f"{done_uri}/.done"] = json.dumps({"starting_message_id": "done"})
    files[f"{done_uri}/.meta.json"] = json.dumps(
        {"phase1": {"status": "ready", "queue_message": {}}}
    )
    files[f"{pending_uri}/messages.jsonl"] = f"{pending.to_jsonl()}\n"
    files[f"{pending_uri}/.meta.json"] = json.dumps(
        {"phase1": {"status": "ready", "queue_message": {}}}
    )
    viking_fs = _MemoryVikingFS(files)
    session = _new_session(viking_fs)
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)
    monkeypatch.setattr(session, "_run_memory_extraction", AsyncMock())

    try:
        result = await session.retry_failed_commit()
        assert [entry["archive_uri"] for entry in result["retried"]] == [failed_uri]
        skipped = {entry["archive_uri"]: entry["reason"] for entry in result["skipped"]}
        assert skipped[done_uri] == "archive_state_completed"
        assert skipped[pending_uri] == "archive_state_pending"
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_retry_without_phase1_queue_message_is_skipped(queue_stub, monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    files = {}
    _write_session_scaffold(files, session_uri, archive_uri)
    # Legacy archives have no Phase 1 snapshot to rebuild the queue message from.
    files[f"{archive_uri}/.meta.json"] = json.dumps({})
    viking_fs = _MemoryVikingFS(files)
    session = _new_session(viking_fs)
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)

    try:
        result = await session.retry_failed_commit()
        assert result["retried"] == []
        assert result["skipped"] == [
            {"archive_uri": archive_uri, "reason": "missing_phase1_queue_message"}
        ]
        # The failed marker must remain intact so the archive stays terminal.
        assert f"{archive_uri}/.failed.json" in viking_fs.files
        assert queue_stub.enqueued == []
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_retry_restores_failed_marker_when_enqueue_fails(queue_stub, monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    files = {}
    _write_session_scaffold(files, session_uri, archive_uri)
    viking_fs = _MemoryVikingFS(files)
    session = _new_session(viking_fs)
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)

    async def _failing_enqueue(queue_name, payload):
        raise RuntimeError("queue down")

    queue_stub.enqueue = _failing_enqueue

    try:
        result = await session.retry_failed_commit()
        assert result["retried"] == []
        assert len(result["failed"]) == 1
        assert result["failed"][0]["archive_uri"] == archive_uri
        # The marker is restored so the archive does not read as pending.
        marker = json.loads(viking_fs.files[f"{archive_uri}/.failed.json"])
        assert marker["stage"] == "memory_extraction"
        assert marker["error"] == "APITimeoutError"
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_retry_specific_archive_uri_only(queue_stub, monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    first_uri = f"{session_uri}/history/archive_001"
    second_uri = f"{session_uri}/history/archive_002"
    files = {}
    _write_session_scaffold(files, session_uri, first_uri)
    _write_session_scaffold(files, session_uri, second_uri)
    files[f"{second_uri}/.failed.json"] = json.dumps(
        {"stage": "memory_extraction", "error": "second", "skipped": True}
    )
    viking_fs = _MemoryVikingFS(files)
    session = _new_session(viking_fs)
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)

    try:
        result = await session.retry_failed_commit(archive_uri=second_uri)
        assert [entry["archive_uri"] for entry in result["retried"]] == [second_uri]
        assert f"{second_uri}/.failed.json" not in viking_fs.files
        # The other failed archive is untouched without an explicit sweep.
        assert f"{first_uri}/.failed.json" in viking_fs.files
        assert len(queue_stub.enqueued) == 1
    finally:
        set_task_tracker(None)
