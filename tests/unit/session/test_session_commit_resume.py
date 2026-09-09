# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import inspect
import json
import threading
from dataclasses import fields
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.service.task_tracker import TaskStatus, TaskTracker, set_task_tracker
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

    async def append_file(self, uri, content, ctx=None):
        self.files[uri] += content


def test_phase2_auto_commit_policy_parameters_are_appended():
    signature = inspect.signature(Session._run_memory_extraction)

    assert list(signature.parameters)[-1] == "auto_commit_policy"
    assert fields(SessionCommitMsg)[-1].name == "auto_commit_policy"


@pytest.mark.asyncio
async def test_resume_queued_commit_continues_phase2(monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    archived = Message(id="archived", role="user", parts=[TextPart("old")])
    retained = Message(id="retained", role="assistant", parts=[TextPart("new")])
    files = {
        f"{session_uri}/messages.jsonl": f"{retained.to_jsonl()}\n",
        f"{session_uri}/.meta.json": json.dumps(
            {"session_id": "session-1", "message_count": 1, "commit_count": 1}
        ),
        f"{archive_uri}/messages.jsonl": f"{archived.to_jsonl()}\n",
    }
    viking_fs = _MemoryVikingFS(files)
    session = Session(viking_fs=viking_fs, session_id="session-1", session_uri=session_uri)
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)
    monkeypatch.setattr(session, "_run_memory_extraction", AsyncMock())
    message = SessionCommitMsg(
        task_id="task-1",
        session_id="session-1",
        session_uri=session_uri,
        archive_uri=archive_uri,
        user={"account_id": "default", "user_id": "default"},
        memory_policy={"memory_types": []},
        auto_commit_policy={"pending_token_threshold": 8000},
    )

    try:
        await session.resume_queued_commit(message)
    finally:
        set_task_tracker(None)

    session._run_memory_extraction.assert_awaited_once()
    assert session._run_memory_extraction.await_args.kwargs["task_id"] == "task-1"
    assert session._run_memory_extraction.await_args.kwargs["agent_evolution_enabled"] is True
    assert session._run_memory_extraction.await_args.kwargs["auto_commit_policy"] == {
        "pending_token_threshold": 8000
    }
    assert [item.id for item in session._run_memory_extraction.await_args.kwargs["messages"]] == [
        "archived"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("archive_content", [None, "{invalid json", "{}"])
async def test_resume_queued_commit_fails_terminally_for_unreadable_archive(
    monkeypatch, archive_content
):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    files = {}
    if archive_content is not None:
        files[f"{archive_uri}/messages.jsonl"] = archive_content
    viking_fs = _MemoryVikingFS(files)
    session = Session(viking_fs=viking_fs, session_id="session-1", session_uri=session_uri)
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)
    monkeypatch.setattr(session, "_run_memory_extraction", AsyncMock())
    message = SessionCommitMsg(
        task_id="task-1",
        session_id="session-1",
        session_uri=session_uri,
        archive_uri=archive_uri,
        user={"account_id": "default", "user_id": "default"},
    )

    try:
        await session.resume_queued_commit(message)
        task = await tracker.get("task-1")
    finally:
        set_task_tracker(None)

    assert task.status == TaskStatus.FAILED
    failed = json.loads(files[f"{archive_uri}/.failed.json"])
    assert failed["stage"] == "archive_read"
    session._run_memory_extraction.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_context_skips_pending_archive_with_missing_messages(monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    session = Session(
        viking_fs=_MemoryVikingFS({}),
        session_id="session-1",
        session_uri=session_uri,
    )
    monkeypatch.setattr(
        session,
        "_list_archive_refs",
        AsyncMock(
            return_value=[{"archive_id": "archive_001", "archive_uri": archive_uri, "index": 1}]
        ),
    )

    context = await session.get_context_for_search(query="test")

    assert context == {"latest_archive_overview": "", "current_messages": []}


@pytest.mark.asyncio
async def test_resume_queued_commit_uses_agent_evolution_archive_snapshot(monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    archived = Message(id="archived", role="user", parts=[TextPart("old")])
    files = {
        f"{session_uri}/messages.jsonl": "",
        f"{session_uri}/.meta.json": json.dumps(
            {"session_id": "session-1", "message_count": 0, "commit_count": 1}
        ),
        f"{archive_uri}/messages.jsonl": f"{archived.to_jsonl()}\n",
        f"{archive_uri}/.meta.json": json.dumps(
            {
                "agent_evolution": {
                    "enabled": False,
                    "skip_reason": "agent_evolution_disabled",
                    "user_config_error": None,
                }
            }
        ),
    }
    session = Session(
        viking_fs=_MemoryVikingFS(files),
        session_id="session-1",
        session_uri=session_uri,
    )
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)
    monkeypatch.setattr(session, "_run_memory_extraction", AsyncMock())
    message = SessionCommitMsg(
        task_id="task-1",
        session_id="session-1",
        session_uri=session_uri,
        archive_uri=archive_uri,
        user={"account_id": "default", "user_id": "default"},
        memory_policy={"memory_types": ["profile"]},
    )

    try:
        await session.resume_queued_commit(message)
    finally:
        set_task_tracker(None)

    kwargs = session._run_memory_extraction.await_args.kwargs
    assert kwargs["agent_evolution_enabled"] is False
    assert kwargs["agent_memory_skip_reason"] == "agent_evolution_disabled"
    assert kwargs["user_config_error"] is None


def test_session_commit_message_ignores_unknown_fields():
    message = SessionCommitMsg.from_dict(
        {
            "task_id": "task-1",
            "session_id": "session-1",
            "session_uri": "viking://user/default/sessions/session-1",
            "archive_uri": "viking://user/default/sessions/session-1/history/archive_001",
            "user": {"account_id": "default", "user_id": "default"},
            "actor_peer_id": "visitor-a",
        }
    )

    assert message.task_id == "task-1"
    assert message.auto_commit_policy == {}
    assert "actor_peer_id" not in message.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_append_keeps_loop_responsive_while_decoding_live_messages(monkeypatch, cancel):
    session_uri = "viking://user/default/sessions/session-1"
    uri = f"{session_uri}/messages.jsonl"
    original = Message(id="old", role="assistant", parts=[TextPart("before\u2028after")])
    content = "\r\n" + original.to_jsonl() + "\r\n"
    fs: Any = _MemoryVikingFS({uri: content})
    fs.append_file = AsyncMock(wraps=fs.append_file)
    session = Session(viking_fs=fs, session_id="session-1", session_uri=session_uri)
    decoded = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    loop = asyncio.get_running_loop()
    from_dict = Message.from_dict

    def slow_decode(data):
        loop.call_soon_threadsafe(decoded.set)
        try:
            # Bound the wait so a regression cannot hang the test runner.
            assert release.wait(2), "live JSONL decoding blocked the event loop"
            return from_dict(data)
        finally:
            finished.set()

    monkeypatch.setattr(Message, "from_dict", slow_decode)
    append = asyncio.create_task(
        session.add_messages_async([{"role": "user", "parts": [TextPart("next")]}])
    )
    try:
        await asyncio.wait_for(decoded.wait(), timeout=5)
        assert not append.done()
        if cancel:
            append.cancel()
            with pytest.raises(asyncio.CancelledError):
                await append
            fs.append_file.assert_not_awaited()
            assert session.messages == []
        release.set()
        if not cancel:
            await append
            assert [message.content for message in session.messages] == [
                "before\u2028after",
                "next",
            ]
            fs.append_file.assert_awaited_once()
        assert await asyncio.to_thread(finished.wait, 2)
        if cancel:
            assert session.messages == []
            assert fs.files[uri] == content
        else:
            assert fs.files[uri] == content + session.messages[-1].to_jsonl() + "\n"
        fs._async_agfs.pathlock_release.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(append, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("row", ["{invalid", '{"role":"user"}'])
async def test_append_rejects_corrupt_live_jsonl_without_writing(row):
    session_uri = "viking://user/default/sessions/session-1"
    uri = f"{session_uri}/messages.jsonl"
    fs: Any = _MemoryVikingFS({uri: "\r\n" + row + "\r\n"})
    fs.append_file = AsyncMock(wraps=fs.append_file)
    session = Session(viking_fs=fs, session_id="session-1", session_uri=session_uri)

    with pytest.raises(ValueError, match="Invalid live message JSONL at line 2") as error:
        await session.add_messages_async([{"role": "user", "parts": [TextPart("next")]}])

    assert error.value.__cause__ is not None
    assert session.messages == []
    fs.append_file.assert_not_awaited()
    fs._async_agfs.pathlock_release.assert_awaited_once()
