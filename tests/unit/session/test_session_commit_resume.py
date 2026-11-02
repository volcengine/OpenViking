# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import inspect
import json
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart, ToolPart
from openviking.pyagfs.exceptions import AGFSConnectionError
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker, set_task_tracker
from openviking.session.memory_policy import MemoryPolicy
from openviking.session.session import Session, SessionMeta
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.parametrize("enabled", [False, True])
def test_new_queue_snapshot_preserves_wm_across_worker_reload(enabled):
    message = SessionCommitMsg(
        task_id="t",
        session_id="s",
        session_uri="viking://user/default/sessions/s",
        archive_uri="viking://user/default/sessions/s/history/archive_001",
        user={},
        memory_policy=MemoryPolicy(enable_working_memory=enabled).to_dict(),
    )
    restored = SessionCommitMsg.from_dict(message.to_dict())
    assert MemoryPolicy.from_dict(restored.memory_policy).enable_working_memory is enabled


def test_legacy_queued_work_keeps_old_default_without_migrating_user_policy():
    policy = {"self": {"enabled": False}, "memory_types": ["profile"]}
    message = SessionCommitMsg.from_dict(
        {
            "task_id": "t",
            "session_id": "s",
            "session_uri": "viking://user/default/sessions/s",
            "archive_uri": "viking://user/default/sessions/s/history/archive_001",
            "user": {},
            "memory_policy": policy,
        }
    )
    assert MemoryPolicy.from_dict(message.memory_policy).enable_working_memory is True
    assert MemoryPolicy.from_dict(policy).enable_working_memory is False
    assert message.memory_policy["self"] == policy["self"]
    assert message.memory_policy["memory_types"] == policy["memory_types"]


class _TaskStore(PersistentTaskStore):
    def __init__(self):
        super().__init__(_FakeAgfs())


class _MemoryVikingFS:
    def __init__(self, files):
        self.files = files
        self._async_agfs = AsyncMock()

    def _uri_to_path(self, uri, ctx=None):
        return "/local/session-1"

    async def read_file(self, uri, ctx=None):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        content = self.files[uri]
        return content.decode("utf-8") if isinstance(content, bytes) else content

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content.decode("utf-8") if isinstance(content, bytes) else content

    async def append_file(self, uri, content, ctx=None):
        self.files[uri] += content

    async def exists(self, uri, ctx=None):
        return uri in self.files or any(path.startswith(f"{uri}/") for path in self.files)

    async def stat(self, uri, ctx=None, skip_count=False):
        if not await self.exists(uri, ctx):
            raise FileNotFoundError(uri)
        return {"path": uri}

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
    monkeypatch, tmp_path, keep_count, keep_turns, archive_count
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
    from openviking.storage.viking_fs import VikingFS
    from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
    from openviking_cli.utils.config.agfs_config import AGFSConfig

    agfs = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
    )
    storage = VikingFS(agfs=agfs)
    tracker = TaskTracker(_TaskStore())
    monkeypatch.setattr("openviking.session.session._enabled_memory_types", lambda: set())
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: SimpleNamespace(enqueue=AsyncMock()),
    )
    try:
        session = Session(viking_fs=storage, session_id="session-1", session_uri=session_uri)
        await session.ensure_exists()
        await storage.write_file(
            f"{session_uri}/messages.jsonl",
            "\n".join(message.to_jsonl() for message in messages),
            ctx=session.ctx,
        )
        turn_options = (
            {"retention_mode": "turn_budget", "keep_recent_turn_count": keep_turns}
            if keep_turns is not None
            else {}
        )
        result = await session.commit_async(keep_recent_count=keep_count, **turn_options)
        archived = await session._archives.read_messages(result["archive_uri"])
        saved = await session._merge_and_save_commit_meta(archive_count, {"events": 1}, None)
        assert saved is not None and saved.memories_extracted["events"] == 1
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
    finally:
        await tracker.close()
        agfs.close()


def test_phase2_auto_commit_policy_parameters_are_appended():
    signature = inspect.signature(Session._run_memory_extraction)

    assert list(signature.parameters)[-1] == "auto_commit_policy"
    # New snapshot metadata is appended after the existing positional fields.
    assert fields(SessionCommitMsg)[-2].name == "auto_commit_policy"
    assert fields(SessionCommitMsg)[-1].name == "memory_policy_version"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation_requested", [False, True])
async def test_phase2_cancellation_marks_archive_only_when_requested(
    monkeypatch, cancellation_requested
):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    tracker = TaskTracker(_TaskStore())
    await tracker.create(
        "session_commit",
        account_id="default",
        user_id="default",
        task_id="task-1",
    )
    if cancellation_requested:
        await tracker.record_cancelled("task-1", account_id="default", user_id="default")
    else:
        await tracker.start("task-1", account_id="default", user_id="default")

    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    files = {}
    session = Session(
        viking_fs=_MemoryVikingFS(files),
        session_id="session-1",
        session_uri=session_uri,
    )

    async def cancel_prepare(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(session, "_prepare_phase2_archive_messages", cancel_prepare)

    with pytest.raises(asyncio.CancelledError):
        await session._run_memory_extraction(
            task_id="task-1",
            archive_uri=archive_uri,
            messages=[],
            first_message_id="",
            last_message_id="",
            memory_policy=None,
        )

    assert (f"{archive_uri}/.failed.json" in files) is cancellation_requested


@pytest.mark.asyncio
async def test_resume_queued_commit_preserves_live_state_when_statistics_are_unavailable(
    monkeypatch,
):
    session_uri = "viking://user/default/sessions/session-1"
    archive_uri = f"{session_uri}/history/archive_001"
    meta_uri = f"{session_uri}/.meta.json"
    live_uri = f"{session_uri}/messages.jsonl"
    archived = Message(id="archived", role="user", parts=[TextPart("archive input")])
    latest_meta = SessionMeta(
        session_id="session-1",
        message_count=7,
        total_message_count=50,
        commit_count=2,
        auto_commit_policy={"keep_recent_count": 3},
    )
    latest_meta.llm_token_usage["total_tokens"] = 100
    original_meta = json.dumps(latest_meta.to_dict())
    files = {
        live_uri: "{live file being rewritten",
        meta_uri: original_meta,
        f"{archive_uri}/messages.jsonl": archived.to_jsonl() + "\n",
        f"{archive_uri}/.meta.json": json.dumps({"phase1": {"status": "ready"}}),
    }
    storage = _MemoryVikingFS(files)
    original_read = storage.read_file

    async def read(uri, ctx=None):
        if uri == meta_uri:
            raise AGFSConnectionError("metadata unavailable")
        return await original_read(uri, ctx)

    reads = AsyncMock(side_effect=read)
    monkeypatch.setattr(storage, "read_file", reads)
    extract = AsyncMock(return_value={"contexts": [SimpleNamespace(category="profile")]})
    summarize = AsyncMock(return_value="# Session Summary\n\nArchive summarized.")
    session = Session(
        viking_fs=storage,
        session_id="session-1",
        session_uri=session_uri,
        session_compressor=SimpleNamespace(extract_long_term_memories=extract),
    )
    # Neither persisted statistics nor a stale instance may supply the fallback.
    session.meta.llm_token_usage["total_tokens"] = 999
    tracker = TaskTracker(_TaskStore())
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    monkeypatch.setattr(session, "_generate_archive_summary_async", summarize)
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(
            memory=SimpleNamespace(extraction_enabled=True, session_skill_extraction_enabled=False)
        ),
    )
    message = SessionCommitMsg(
        task_id="task-1",
        session_id=session.session_id,
        session_uri=session_uri,
        archive_uri=archive_uri,
        user=session.user.to_dict(),
        memory_policy=MemoryPolicy(memory_types={"profile"}, enable_working_memory=True).to_dict(),
    )
    processor = SessionCommitProcessor(SimpleNamespace(session=lambda *args, **kwargs: session))

    await processor.on_dequeue(message.to_dict())

    task = await tracker.get(message.task_id)
    assert task.status is TaskStatus.COMPLETED
    assert task.result["memories_extracted"] == {"profile": 1}
    assert task.result["token_usage"] == {
        "llm": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
        },
        "embedding": {"total_tokens": 0},
        "total": {"total_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0},
    }
    assert files[meta_uri] == original_meta
    assert f"{archive_uri}/.done" in files
    assert [message.id for message in summarize.await_args.args[0]] == ["archived"]
    assert [message.id for message in extract.await_args.kwargs["messages"]] == ["archived"]
    assert extract.await_args.kwargs["allowed_memory_types"] == {"profile"}

    await processor.on_dequeue(message.to_dict())

    extract.assert_awaited_once()
    summarize.assert_awaited_once()
    assert all(call.args[0] != live_uri for call in reads.await_args_list)
    assert files[meta_uri] == original_meta


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
