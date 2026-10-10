# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A running session commit that stops progressing must not block its successors."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openviking.message import Message, TextPart
from openviking.models.vlm.base import VLMBase
from openviking.models.vlm.token_usage import TokenUsageTracker
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_work_index import bind_task_context
from openviking.session import session as session_module

STALL_TIMEOUT = 0.3


def _text_message(message_id: str, text: str) -> Message:
    return Message(id=message_id, role="user", parts=[TextPart(text)])


async def _write_queued_archive(session, index: int, task_id: str, messages: list[Message]):
    archive_uri = f"{session.uri}/history/archive_{index:03d}"
    await session._viking_fs.write_file(
        f"{archive_uri}/messages.jsonl",
        "\n".join(message.to_jsonl() for message in messages) + "\n",
        ctx=session.ctx,
    )
    await session._viking_fs.write_file(
        f"{archive_uri}/.meta.json",
        json.dumps({"phase1": {"status": "ready", "queue_message": {"task_id": task_id}}}),
        ctx=session.ctx,
    )
    return archive_uri


async def _read_failed_marker(session, archive_uri: str) -> dict | None:
    if not await session._archive_file_exists(archive_uri, ".failed.json"):
        return None
    return json.loads(
        await session._viking_fs.read_file(f"{archive_uri}/.failed.json", ctx=session.ctx)
    )


async def _wait_for_status(tracker, task_id: str, ctx, status: TaskStatus) -> None:
    async def _poll():
        while True:
            task = await tracker.get(task_id, account_id=ctx.account_id, user_id=ctx.user.user_id)
            if task is not None and task.status == status:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=5)


@pytest.fixture
def stall_timeout(monkeypatch):
    monkeypatch.setattr(
        session_module,
        "_stalled_predecessor_timeout_seconds",
        lambda: STALL_TIMEOUT,
    )
    session_module._stalled_predecessor_warned_at.clear()
    yield STALL_TIMEOUT
    session_module._stalled_predecessor_warned_at.clear()


@pytest.fixture
def recorded_logger(monkeypatch):
    recorder = MagicMock(wraps=session_module.logger)
    monkeypatch.setattr(session_module, "logger", recorder)
    return recorder


async def _start_tracked_task(tracker, task_id: str, ctx) -> None:
    await tracker.create(
        "session_commit",
        resource_id="stall",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        task_id=task_id,
    )


async def test_stalled_running_predecessor_is_cancelled_and_successor_proceeds(
    client,
    monkeypatch,
    stall_timeout,
    recorded_logger,
):
    session = client(session_id="commit_stall_reaped")
    await session.ensure_exists()
    head_messages = [_text_message("u1", "stuck head")]
    head_uri = await _write_queued_archive(session, 1, "stall-head", head_messages)
    tracker = get_task_tracker()
    ctx = session.ctx
    await _start_tracked_task(tracker, "stall-head", ctx)

    async def _hang_forever(messages):
        await asyncio.Event().wait()

    # Phase 2 hangs after the task enters RUNNING, without any progress signal.
    monkeypatch.setattr(session._tool_outputs, "hydrate_for_extraction", _hang_forever)

    async def _run_head():
        tracker.register_running_task("stall-head")
        try:
            await session._run_memory_extraction(
                task_id="stall-head",
                archive_uri=head_uri,
                messages=head_messages,
                first_message_id="u1",
                last_message_id="u1",
                memory_policy=None,
            )
        except asyncio.CancelledError:
            if not tracker.is_cancellation_requested("stall-head"):
                raise
        finally:
            await tracker.unregister_running_task("stall-head")

    head = asyncio.create_task(_run_head())
    await _wait_for_status(tracker, "stall-head", ctx, TaskStatus.RUNNING)

    # A freshly running head blocks its successor as before.
    assert not await session._can_run_archive(2)
    recorded_logger.error.assert_not_called()

    await asyncio.sleep(stall_timeout + 0.1)
    # The first check past the timeout cancels the head but waits for it to unwind.
    assert not await session._can_run_archive(2)
    await asyncio.wait_for(head, timeout=5)

    task = await tracker.get("stall-head", account_id=ctx.account_id, user_id=ctx.user.user_id)
    assert task.status == TaskStatus.CANCELLED
    assert "no progress" in task.error
    failed = await _read_failed_marker(session, head_uri)
    assert failed["stage"] == "cancelled"
    assert "no progress" in failed["error"]
    assert any(
        "Cancelling stalled session commit" in call.args[0]
        for call in recorded_logger.error.call_args_list
    )

    # The head is terminal now, so the successor runs.
    assert await session._can_run_archive(2)


async def test_long_running_predecessor_with_model_heartbeats_is_not_cancelled(
    client,
    stall_timeout,
    recorded_logger,
):
    session = client(session_id="commit_stall_alive")
    await session.ensure_exists()
    head_uri = await _write_queued_archive(session, 1, "alive-head", [_text_message("u1", "a")])
    tracker = get_task_tracker()
    ctx = session.ctx
    await _start_tracked_task(tracker, "alive-head", ctx)
    await tracker.start("alive-head", account_id=ctx.account_id, user_id=ctx.user.user_id)
    model = SimpleNamespace(_token_tracker=TokenUsageTracker())
    stop = asyncio.Event()

    async def _alive_head():
        tracker.register_running_task("alive-head")
        try:
            with bind_task_context("alive-head", ctx.account_id, ctx.user.user_id):
                while not stop.is_set():
                    # Each successful model response reports progress for the bound task.
                    VLMBase.update_token_usage(
                        model,
                        model_name="m",
                        provider="p",
                        prompt_tokens=1,
                        completion_tokens=1,
                    )
                    await asyncio.sleep(stall_timeout / 4)
        finally:
            tracker._work_index.unregister_active("alive-head", asyncio.current_task())

    head = asyncio.create_task(_alive_head())
    try:
        # Run for several stall timeouts in total.
        for _ in range(int(3 * stall_timeout / 0.05)):
            assert not await session._can_run_archive(2)
            await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(head, timeout=5)

    task = await tracker.get("alive-head", account_id=ctx.account_id, user_id=ctx.user.user_id)
    assert task.status == TaskStatus.RUNNING
    assert task.error is None
    assert await _read_failed_marker(session, head_uri) is None
    recorded_logger.error.assert_not_called()
    recorded_logger.warning.assert_not_called()


async def test_queued_predecessor_not_executing_here_is_not_cancelled(
    client,
    monkeypatch,
    stall_timeout,
):
    """A RUNNING record restored after a restart waits for its own redelivery."""
    session = client(session_id="commit_stall_not_executing")
    await session.ensure_exists()
    head_uri = await _write_queued_archive(session, 1, "queued-head", [_text_message("u1", "a")])
    tracker = get_task_tracker()
    ctx = session.ctx
    await _start_tracked_task(tracker, "queued-head", ctx)
    await tracker.start("queued-head", account_id=ctx.account_id, user_id=ctx.user.user_id)
    original_has_work = tracker.has_work
    monkeypatch.setattr(
        tracker,
        "has_work",
        lambda task_id: task_id == "queued-head" or original_has_work(task_id),
    )

    await asyncio.sleep(stall_timeout + 0.1)

    assert not await session._can_run_archive(2)
    task = await tracker.get("queued-head", account_id=ctx.account_id, user_id=ctx.user.user_id)
    assert task.status == TaskStatus.RUNNING
    assert await _read_failed_marker(session, head_uri) is None


async def test_blocked_successor_is_logged_when_cancellation_is_disabled(
    client,
    monkeypatch,
    recorded_logger,
):
    monkeypatch.setattr(session_module, "_stalled_predecessor_timeout_seconds", lambda: 0.0)
    monkeypatch.setattr(session_module, "_STALLED_PREDECESSOR_WARN_SECONDS", 0.1)
    session_module._stalled_predecessor_warned_at.clear()
    session = client(session_id="commit_stall_disabled")
    await session.ensure_exists()
    head_uri = await _write_queued_archive(session, 1, "disabled-head", [_text_message("u1", "a")])
    tracker = get_task_tracker()
    ctx = session.ctx
    await _start_tracked_task(tracker, "disabled-head", ctx)
    await tracker.start("disabled-head", account_id=ctx.account_id, user_id=ctx.user.user_id)
    release = asyncio.Event()

    async def _hung_head():
        tracker.register_running_task("disabled-head")
        try:
            await release.wait()
        finally:
            tracker._work_index.unregister_active("disabled-head", asyncio.current_task())

    head = asyncio.create_task(_hung_head())
    try:
        await asyncio.sleep(0.2)
        assert not await session._can_run_archive(2)
        assert not await session._can_run_archive(2)
    finally:
        release.set()
        await asyncio.wait_for(head, timeout=5)

    warnings = [
        call
        for call in recorded_logger.warning.call_args_list
        if "commit chain for session" in call.args[0]
    ]
    assert len(warnings) == 1, "repeated requeues must not flood the log"
    assert "disabled-head" in warnings[0].args
    task = await tracker.get("disabled-head", account_id=ctx.account_id, user_id=ctx.user.user_id)
    assert task.status == TaskStatus.RUNNING
    assert await _read_failed_marker(session, head_uri) is None
    session_module._stalled_predecessor_warned_at.clear()


async def test_predecessor_ignoring_cancellation_is_abandoned_after_another_timeout(
    client,
    stall_timeout,
    recorded_logger,
):
    session = client(session_id="commit_stall_abandoned")
    await session.ensure_exists()
    head_uri = await _write_queued_archive(session, 1, "stubborn-head", [_text_message("u1", "a")])
    tracker = get_task_tracker()
    ctx = session.ctx
    await _start_tracked_task(tracker, "stubborn-head", ctx)
    await tracker.start("stubborn-head", account_id=ctx.account_id, user_id=ctx.user.user_id)
    release = asyncio.Event()

    async def _stubborn_head():
        tracker.register_running_task("stubborn-head")
        try:
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
        finally:
            tracker._work_index.unregister_active("stubborn-head", asyncio.current_task())

    head = asyncio.create_task(_stubborn_head())
    try:
        await asyncio.sleep(stall_timeout + 0.1)
        assert not await session._can_run_archive(2)
        await _wait_for_status(tracker, "stubborn-head", ctx, TaskStatus.CANCELLING)
        assert not await session._can_run_archive(2)

        await asyncio.sleep(stall_timeout + 0.1)
        assert await session._can_run_archive(2)
    finally:
        release.set()
        await asyncio.wait_for(head, timeout=5)

    failed = await _read_failed_marker(session, head_uri)
    assert failed["stage"] == "stalled"
    assert "no progress" in failed["error"]
    assert any(
        "Abandoning session commit" in call.args[0] for call in recorded_logger.error.call_args_list
    )
