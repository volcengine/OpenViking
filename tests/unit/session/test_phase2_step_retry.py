# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Phase 2 step-level retry against stall-class (timeout) failures.

Regression tests for #5513: an Ark streaming call failing with
``ArkAPITimeoutError`` ("Request timed out.") classified as ``unknown``
instead of transient, so neither the VLM call-level retry nor the Phase 2
step-level retry ever engaged and the first stalled request failed the whole
commit task.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from volcenginesdkarkruntime._exceptions import ArkAPITimeoutError

from openviking.message import Message, TextPart
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking.session.memory_policy import MemoryPolicy
from openviking.session.session import Session
from openviking.utils.model_retry import (
    classify_api_error,
    is_retryable_api_error,
    is_stall_api_error,
    retry_async,
    stall_retry_delay,
)


def _ark_timeout() -> ArkAPITimeoutError:
    return ArkAPITimeoutError("Request timed out.", "req-5513")


def _message(message_id: str, text: str = "remember this") -> Message:
    return Message(id=message_id, role="user", parts=[TextPart(text)])


class _TaskStore:
    def __init__(self):
        self.tasks = {}

    async def create(self, task):
        self.tasks[task.task_id] = task

    async def update(self, task):
        self.tasks[task.task_id] = task

    async def get(self, task_id, *, account_id=None, user_id=None):
        return self.tasks.get(task_id)

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

    async def exists(self, uri, ctx=None):
        return uri in self.files


def _flaky_compressor(errors, payload):
    """Compressor whose extract call raises each error in order, then succeeds."""
    calls = {"count": 0}

    class _Compressor:
        async def extract_long_term_memories(self, **kwargs):
            calls["count"] += 1
            if calls["count"] <= len(errors):
                raise errors[calls["count"] - 1]
            return payload

    return _Compressor(), calls


def _make_session(files, compressor):
    session_uri = "viking://user/default/sessions/session-1"
    return Session(
        viking_fs=_MemoryVikingFS(files),
        session_id="session-1",
        session_uri=session_uri,
        session_compressor=compressor,
    )


async def _run_phase2(monkeypatch, session, task_id="task-5513"):
    tracker = TaskTracker(_TaskStore())
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    await tracker.create("session_commit", account_id="default", user_id="default", task_id=task_id)
    archive_uri = f"{session._session_uri}/history/archive_001"
    messages = [_message("m1"), _message("m2")]

    async def prepare(uri, archived):
        return archived, None, None, [], {}

    monkeypatch.setattr(session, "_prepare_phase2_archive_messages", prepare)
    await session._run_memory_extraction(
        task_id=task_id,
        archive_uri=archive_uri,
        messages=messages,
        first_message_id="m1",
        last_message_id="m2",
        memory_policy=MemoryPolicy(enable_working_memory=False).to_dict(),
    )
    return await tracker.get(task_id), archive_uri


# ---------------------------------------------------------------------------
# Classifier: "Request timed out." must be retryable
# ---------------------------------------------------------------------------


def test_ark_request_timed_out_is_transient():
    error = _ark_timeout()
    assert classify_api_error(error) == "transient"
    assert is_retryable_api_error(error) is True


def test_bare_timeout_exception_is_transient():
    assert classify_api_error(TimeoutError("timed out")) == "transient"
    assert classify_api_error(asyncio.TimeoutError()) == "transient"


def test_stall_detection_covers_timeout_and_connection_shapes():
    assert is_stall_api_error(_ark_timeout()) is True
    assert is_stall_api_error(TimeoutError("timed out")) is True
    assert is_stall_api_error(RuntimeError("connection reset by peer")) is True
    wrapped = RuntimeError("call failed")
    wrapped.__cause__ = TimeoutError("timed out")
    assert is_stall_api_error(wrapped) is True


def test_non_stall_errors_are_not_stall_class():
    assert is_stall_api_error(RuntimeError("503 Service Unavailable")) is False
    assert is_stall_api_error(RuntimeError("429 TooManyRequests")) is False
    assert is_stall_api_error(RuntimeError("400 invalid parameter")) is False


def test_stall_retry_delay_is_bounded_and_growing():
    first = stall_retry_delay(0)
    second = stall_retry_delay(1)
    assert 24.0 <= first <= 36.0
    assert 48.0 <= second <= 72.0
    assert stall_retry_delay(9) <= 120.0 * 1.2


@pytest.mark.asyncio
async def test_retry_async_delay_for_hook_overrides_backoff(monkeypatch):
    attempts = []
    delays = []

    async def flaky():
        attempts.append(len(attempts))
        if len(attempts) < 3:
            raise _ark_timeout()
        return "ok"

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    result = await retry_async(
        flaky,
        max_retries=3,
        delay_for=lambda exc, attempt: (
            stall_retry_delay(attempt) if is_stall_api_error(exc) else None
        ),
    )

    assert result == "ok"
    assert len(attempts) == 3
    assert len(delays) == 2
    assert all(delay >= 24.0 for delay in delays)


# ---------------------------------------------------------------------------
# Phase 2 end to end: the step-level retry rescues a transient stall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase2_survives_two_ark_timeouts(monkeypatch):
    payload = {
        "contexts": [SimpleNamespace(category="profile")],
        "session_skills": [],
    }
    compressor, calls = _flaky_compressor([_ark_timeout(), _ark_timeout()], payload)
    files = {}
    session = _make_session(files, compressor)
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    task, archive_uri = await _run_phase2(monkeypatch, session)

    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert calls["count"] == 3
    assert len(delays) == 2
    assert all(delay >= 24.0 for delay in delays)
    assert f"{archive_uri}/.done" in files
    assert f"{archive_uri}/.failed.json" not in files
    done = json.loads(files[f"{archive_uri}/.done"])
    assert done["starting_message_id"] == "m1"
    assert done["ending_message_id"] == "m2"
    assert done["completed_memory_steps"]["long_term"] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_phase2_persistent_timeout_still_fails_via_existing_path(monkeypatch):
    payload = {"contexts": [], "session_skills": []}
    compressor, calls = _flaky_compressor([_ark_timeout() for _ in range(10)], payload)
    files = {}
    session = _make_session(files, compressor)
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    task, archive_uri = await _run_phase2(monkeypatch, session)

    # 1 initial call + _MEMORY_EXTRACTION_MAX_RETRIES (3) step-level retries,
    # each separated by stall-scale backoff (30s base, capped at 120s).
    assert calls["count"] == 4
    assert len(delays) == 3
    assert all(delay >= 24.0 for delay in delays)
    assert task.status == TaskStatus.FAILED
    assert f"{archive_uri}/.done" not in files
    failed = json.loads(files[f"{archive_uri}/.failed.json"])
    assert failed["stage"] == "memory_extraction"
    assert "Request timed out." in failed["error"]


@pytest.mark.asyncio
async def test_phase2_fast_transient_keeps_short_backoff(monkeypatch):
    payload = {"contexts": [], "session_skills": []}
    compressor, calls = _flaky_compressor(
        [
            RuntimeError("503 Service Unavailable"),
            RuntimeError("503 Service Unavailable"),
        ],
        payload,
    )
    session = _make_session({}, compressor)
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    task, _ = await _run_phase2(monkeypatch, session)

    assert task.status == TaskStatus.COMPLETED
    assert calls["count"] == 3
    assert delays, "fast transients should still back off"
    assert all(delay <= 10.0 for delay in delays)
