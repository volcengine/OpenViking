# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Commit Phase 2 wiring of memory usage feedback."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.message.part import ContextPart
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory_policy import MemoryPolicy
from openviking.session.session import Session
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking.storage.queuefs.session_commit_processor import SessionCommitProcessor
from tests.unit.session.test_session_commit_resume import _MemoryVikingFS, _TaskStore

SESSION_URI = "viking://user/default/sessions/session-1"
ARCHIVE_URI = f"{SESSION_URI}/history/archive_001"
MEMORY_URI = "viking://user/default/memories/preferences/editor.md"


class _FakeVLM:
    def __init__(self, response="", error=None):
        self.response = response
        self.error = error
        self.calls = 0

    async def get_completion_async(self, prompt):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def _setup(monkeypatch, vlm, extract=None, enabled=True):
    archived = Message(
        id="u1",
        role="user",
        parts=[TextPart("format my file"), ContextPart(uri=MEMORY_URI, context_type="memory")],
    )
    original = MemoryFileUtils.write(
        MemoryFile(uri=MEMORY_URI, memory_type="preferences", content="Prefers tabs.")
    )
    files = {
        f"{SESSION_URI}/messages.jsonl": "",
        f"{ARCHIVE_URI}/messages.jsonl": archived.to_jsonl() + "\n",
        f"{ARCHIVE_URI}/.meta.json": json.dumps({"phase1": {"status": "ready"}}),
        MEMORY_URI: original,
    }
    session = Session(
        viking_fs=_MemoryVikingFS(files),
        session_id="session-1",
        session_uri=SESSION_URI,
        session_compressor=SimpleNamespace(
            extract_long_term_memories=extract or AsyncMock(return_value={"contexts": []})
        ),
    )
    tracker = TaskTracker(_TaskStore())
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(
            memory=SimpleNamespace(
                extraction_enabled=True,
                session_skill_extraction_enabled=False,
                usage_feedback_enabled=enabled,
            )
        ),
    )
    monkeypatch.setattr(session, "_get_vlm_config", AsyncMock(return_value=vlm))
    message = SessionCommitMsg(
        task_id="task-1",
        session_id=session.session_id,
        session_uri=SESSION_URI,
        archive_uri=ARCHIVE_URI,
        user=session.user.to_dict(),
        memory_policy=MemoryPolicy(memory_types={"profile"}, enable_working_memory=False).to_dict(),
    )
    processor = SessionCommitProcessor(SimpleNamespace(session=lambda *args, **kwargs: session))
    return files, original, tracker, message, processor


@pytest.mark.asyncio
async def test_commit_scores_used_memory_and_does_not_rescore_on_resume(monkeypatch):
    vlm = _FakeVLM(json.dumps([{"uri": MEMORY_URI, "score": -1, "reason": "user rejected tabs"}]))
    files, _original, tracker, message, processor = _setup(monkeypatch, vlm)

    await processor.on_dequeue(message.to_dict())

    assert (await tracker.get(message.task_id)).status is TaskStatus.COMPLETED
    stored = MemoryFileUtils.read(files[MEMORY_URI], uri=MEMORY_URI)
    stats = stored.extra_fields["usage_feedback"]
    assert (stats["value"], stats["harmful"], stats["helpful"], stats["unused"]) == (-1, 1, 0, 0)
    assert stats["last_archive"] == "session-1/archive_001"
    assert stored.content == "Prefers tabs."
    done = json.loads(files[f"{ARCHIVE_URI}/.done"])
    assert done["completed_memory_steps"]["usage_feedback"] == ["u1"]
    assert json.loads(files[f"{ARCHIVE_URI}/.meta.json"])["usage_feedback"] == {MEMORY_URI: -1}

    await processor.on_dequeue(message.to_dict())
    assert vlm.calls == 1


@pytest.mark.asyncio
async def test_scoring_failure_leaves_commit_and_memory_intact(monkeypatch):
    files, original, tracker, message, processor = _setup(
        monkeypatch, _FakeVLM(error=RuntimeError("boom"))
    )

    await processor.on_dequeue(message.to_dict())

    assert (await tracker.get(message.task_id)).status is TaskStatus.COMPLETED
    assert files[MEMORY_URI] == original


@pytest.mark.asyncio
async def test_disabled_feedback_never_calls_the_judge(monkeypatch):
    vlm = _FakeVLM("[]")
    files, original, tracker, message, processor = _setup(monkeypatch, vlm, enabled=False)

    await processor.on_dequeue(message.to_dict())

    assert (await tracker.get(message.task_id)).status is TaskStatus.COMPLETED
    assert vlm.calls == 0
    assert files[MEMORY_URI] == original


@pytest.mark.asyncio
async def test_extraction_failure_cancels_scoring_without_writing(monkeypatch):
    started = asyncio.Event()

    class _SlowVLM(_FakeVLM):
        async def get_completion_async(self, prompt):
            started.set()
            await asyncio.sleep(3600)

    async def failing_extract(**_kwargs):
        await started.wait()
        raise ValueError("extraction broke")

    files, original, tracker, message, processor = _setup(
        monkeypatch, _SlowVLM(), extract=failing_extract
    )

    await asyncio.wait_for(processor.on_dequeue(message.to_dict()), timeout=30)

    assert (await tracker.get(message.task_id)).status is TaskStatus.FAILED
    assert files[MEMORY_URI] == original
    assert f"{ARCHIVE_URI}/.done" not in files
