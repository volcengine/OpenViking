# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import inspect
import json
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart, ToolPart
from openviking.pyagfs.exceptions import AGFSNetworkError, AGFSTimeoutError
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

    async def read_file(self, uri, ctx=None, *, include_expired=False):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content

    async def append_file(self, uri, content, ctx=None):
        self.files[uri] += content

    async def exists(self, uri, ctx=None, *, include_expired=False):
        return uri in self.files or any(path.startswith(f"{uri}/") for path in self.files)

    async def ls(self, uri, ctx=None):
        prefix = f"{uri}/"
        names = {
            path[len(prefix) :].split("/")[0] for path in self.files if path.startswith(prefix)
        }
        return [{"name": name} for name in sorted(names)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "keep_count, keep_turns, archive_count",
    [(10, None, 5), (10, 1, 2), (10, 0, 15), (0, None, 15)],
)
async def test_commit_retention_boundary_and_pending_tokens_after_reload(
    monkeypatch, keep_count, keep_turns, archive_count
):
    session_uri = "viking://user/default/sessions/session-1"
    messages = [
        Message(id="old-user", role="user", parts=[TextPart("old question")]),
        Message(id="old-assistant", role="assistant", parts=[TextPart("old answer")]),
        Message(id="latest-user", role="user", parts=[TextPart("latest question")]),
        *[
            Message(
                id=f"step-{i}",
                role="assistant",
                parts=[
                    ToolPart(tool_id=f"tool-{i}", tool_name="read", tool_output=f"result {i}"),
                ],
            )
            for i in range(12)
        ],
    ]
    storage = _MemoryVikingFS(
        {
            f"{session_uri}/messages.jsonl": "\n".join(message.to_jsonl() for message in messages),
        }
    )
    tracker = TaskTracker(_TaskStore())
    monkeypatch.setattr("openviking.session.session._enabled_memory_types", lambda: set())
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(enqueue=AsyncMock()),
    )
    session = Session(viking_fs=storage, session_id="session-1", session_uri=session_uri)
    turn_options = (
        {"retention_mode": "turn_budget", "keep_recent_turn_count": keep_turns}
        if keep_turns is not None
        else {}
    )
    result = await session.commit_async(keep_recent_count=keep_count, **turn_options)
    archived = await session._archives.read_messages(result["archive_uri"])
    retained = messages[archive_count:]
    assert [message.id for message in archived] == [m.id for m in messages[:archive_count]]
    assert [message.id for message in session.messages] == [m.id for m in retained]
    assert session.meta.pending_tokens == 0

    reloaded = Session(viking_fs=storage, session_id="session-1", session_uri=session_uri)
    await reloaded.load()
    assert reloaded.meta.pending_tokens == 0
    appended = await reloaded.add_message_async("user", [TextPart("next question")])
    if keep_turns == 0 or keep_count == 0:
        expected = appended.estimated_tokens
    elif keep_turns == 1:
        expected = sum(message.estimated_tokens for message in retained)
    else:
        expected = retained[0].estimated_tokens
    assert reloaded.meta.pending_tokens == expected
    fresh = Session(viking_fs=storage, session_id="session-1", session_uri=session_uri)
    await fresh.load()
    assert fresh.meta.pending_tokens == expected


def test_phase2_auto_commit_policy_parameters_are_appended():
    signature = inspect.signature(Session._run_memory_extraction)

    assert list(signature.parameters)[-2:] == ["auto_commit_policy", "ttl_generation"]
    assert [item.name for item in fields(SessionCommitMsg)[-2:]] == [
        "auto_commit_policy",
        "ttl_generation",
    ]


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
async def test_phase1_does_not_renew_frozen_ttl(monkeypatch):
    session_uri = "viking://user/default/sessions/session-1"
    message = Message(id="old-user", role="user", parts=[TextPart("old question")])
    storage = _MemoryVikingFS(
        {f"{session_uri}/messages.jsonl": f"{message.to_jsonl()}\n"}
    )
    tracker = TaskTracker(_TaskStore())
    monkeypatch.setattr("openviking.session.session._enabled_memory_types", lambda: set())
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(enqueue=AsyncMock()),
    )
    session = Session(viking_fs=storage, session_id="session-1", session_uri=session_uri)
    session.meta.ttl_days = 7
    session.meta.received_at = "2999-01-01T00:00:00.000Z"
    session.meta.expires_at = "2999-01-08T00:00:00.000Z"
    session.meta.ttl_generation = "generation-1"

    result = await session.commit_async()

    assert result["status"] == "accepted"
    assert session.meta.received_at == "2999-01-01T00:00:00.000Z"
    assert session.meta.expires_at == "2999-01-08T00:00:00.000Z"


@pytest.mark.asyncio
async def test_phase2_completion_renews_from_one_persisted_timestamp():
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    files = {
        f"{session_uri}/messages.jsonl": "",
        f"{session_uri}/.meta.json": json.dumps(
            {
                "session_id": "session-1",
                "ttl_days": 2,
                "received_at": "2999-01-01T00:00:00.000Z",
                "expires_at": "2999-01-03T00:00:00.000Z",
                "ttl_generation": "generation-1",
            }
        ),
        f"{archive_uri}/.meta.json": json.dumps(
            {"phase2_completed_at": "2999-02-03T04:05:06.000Z"}
        ),
    }
    session = Session(
        viking_fs=_MemoryVikingFS(files),
        session_id="session-1",
        session_uri=session_uri,
    )
    await session.load(include_expired=True)

    completed_at = await session._merge_and_save_commit_meta(
        archive_uri=archive_uri,
        archive_index=1,
        memories_extracted={},
        telemetry_snapshot=None,
        ttl_generation="generation-1",
    )

    assert completed_at == "2999-02-03T04:05:06.000Z"
    persisted = json.loads(files[f"{session_uri}/.meta.json"])
    assert persisted["received_at"] == "2999-01-01T00:00:00.000Z"
    assert persisted["expires_at"] == "2999-02-05T04:05:06.000Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [AGFSNetworkError, AGFSTimeoutError])
async def test_phase2_final_meta_read_outage_is_not_stale(error_type):
    uri = "viking://user/default/sessions/session-1"
    metadata = json.dumps({"ttl_generation": "g1", "expires_at": "2999-01-01T00:00:00Z"})
    storage = _MemoryVikingFS({uri + "/.meta.json": metadata})
    # The fence read succeeds; the subsequent merge read fails under the same lock.
    storage.read_file = AsyncMock(side_effect=[metadata, error_type("endpoint not found")])
    session = Session(viking_fs=storage, session_id="session-1", session_uri=uri)
    with pytest.raises(error_type, match="endpoint not found"):
        await session._merge_and_save_commit_meta(
            archive_index=1,
            memories_extracted={},
            telemetry_snapshot=None,
            ttl_generation="g1",
        )
    assert storage.files == {uri + "/.meta.json": metadata}


@pytest.mark.asyncio
async def test_done_recovery_repairs_ttl_with_original_completion_time():
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    completed_at = "2026-02-03T04:05:06.000Z"
    files = {
        f"{session_uri}/messages.jsonl": "",
        f"{session_uri}/.meta.json": json.dumps(
            {
                "session_id": "session-1",
                "ttl_days": 2,
                "received_at": "2026-01-01T00:00:00.000Z",
                "expires_at": "2026-01-03T00:00:00.000Z",
                "ttl_generation": "generation-1",
            }
        ),
        f"{archive_uri}/.done": json.dumps(
            {
                "phase2_completed_at": completed_at,
                "ttl_generation": "generation-1",
            }
        ),
    }
    storage = _MemoryVikingFS(files)
    session = Session(viking_fs=storage, session_id="session-1", session_uri=session_uri)
    await session.load(include_expired=True)
    tracker = TaskTracker(_TaskStore())
    set_task_tracker(tracker)
    message = SessionCommitMsg(
        task_id="task-done",
        session_id="session-1",
        session_uri=session_uri,
        archive_uri=archive_uri,
        user={"account_id": "default", "user_id": "default"},
        ttl_generation="generation-1",
    )

    try:
        assert await session.resume_queued_commit(message) is True
    finally:
        set_task_tracker(None)

    persisted = json.loads(files[f"{session_uri}/.meta.json"])
    assert persisted["received_at"] == "2026-01-01T00:00:00.000Z"
    assert persisted["expires_at"] == "2026-02-05T04:05:06.000Z"


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
@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize(
    "error_type,message",
    [
        (TimeoutError, "session metadata unavailable"),
        (AGFSNetworkError, "endpoint not found"),
        (AGFSTimeoutError, "backend not found before timeout"),
        (ConnectionError, "DNS name not found"),
    ],
)
async def test_phase2_metadata_outage_never_completes_as_stale(
    monkeypatch, persistent, error_type, message
):
    uri = "viking://user/default/sessions/session-1"
    archive = uri + "/history/archive_001"
    storage = _MemoryVikingFS(
        {
            uri + "/.meta.json": json.dumps(
                {"ttl_generation": "g1", "expires_at": "2999-01-01T00:00:00Z"}
            )
        }
    )
    read = storage.read_file
    attempts = 0

    async def unreliable_read(uri, **kwargs):
        nonlocal attempts
        attempts += 1
        if persistent or attempts == 1:
            raise error_type(message)
        return await read(uri, **kwargs)

    storage.read_file = unreliable_read
    session = Session(viking_fs=storage, session_id="session-1", session_uri=uri)
    tracker = TaskTracker(_TaskStore())
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(session, "_prepare_phase2_archive_messages", AsyncMock())
    task = await tracker.create(
        "session_commit",
        task_id="ttl-outage",
        resource_id="session-1",
        account_id=session.ctx.account_id,
        user_id=session.ctx.user.user_id,
    )

    extraction = session._run_memory_extraction(
        task_id=task.task_id,
        archive_uri=archive,
        messages=[],
        first_message_id="first",
        last_message_id="last",
        memory_policy={},
        ttl_generation="g1",
    )
    if persistent:
        # Failure-marker fencing is also unavailable: raise so QueueFS cannot
        # acknowledge this delivery and restart recovery can retry it.
        with pytest.raises(error_type, match=message):
            await extraction
        assert archive + "/.failed.json" not in storage.files
    else:
        await extraction
        failure = json.loads(storage.files[archive + "/.failed.json"])
        assert failure["error"] == message
    task = await tracker.get(
        task.task_id, account_id=session.ctx.account_id, user_id=session.ctx.user.user_id
    )
    if not persistent:
        assert task.status == TaskStatus.FAILED
    assert task.status != TaskStatus.COMPLETED
    assert archive + "/.done" not in storage.files
    session._prepare_phase2_archive_messages.assert_not_awaited()


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
        session._archives,
        "list_refs",
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
            "usage_uris": ["viking://resources/legacy"],
        }
    )

    assert message.task_id == "task-1"
    assert message.auto_commit_policy == {}
    assert "actor_peer_id" not in message.to_dict()
    assert "usage_uris" not in message.to_dict()
