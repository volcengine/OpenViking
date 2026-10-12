# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory semantic queue stall fix (issue #864).

Ensures that _process_memory_directory() error paths propagate exceptions
so that on_dequeue() returns an explicit processing outcome.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker


def _processor():
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace()))
    return SemanticProcessor(vlm_resolver=resolver)


def _make_msg(uri="viking://user/usr1/memories", context_type="memory", **kwargs):
    """Build a minimal SemanticMsg for testing."""
    defaults = {
        "id": "test-msg-1",
        "uri": uri,
        "context_type": context_type,
        "recursive": False,
        "role": "root",
        "account_id": "acc1",
        "user_id": "usr1",
        "peer_id": "test-peer",
        "telemetry_id": "",
        "target_uri": "",
        "changes": None,
        "is_code_repo": False,
    }
    defaults.update(kwargs)
    return SemanticMsg.from_dict(defaults)


def _build_data(msg: SemanticMsg) -> dict:
    """Wrap a SemanticMsg into the dict format on_dequeue expects."""
    return msg.to_dict()


@pytest.mark.parametrize("context_type", ["resource", "memory", "skill"])
async def test_retry_cancellation_keeps_skill_wait_isolated(monkeypatch, context_type):
    processor = _processor()
    entered = asyncio.Event()

    async def wait_until_ready(**kwargs):
        entered.set()
        await asyncio.Future()

    processor._circuit_breakers["acc1"] = SimpleNamespace(
        wait_until_ready=wait_until_ready,
        abandon=MagicMock(),
        retry_after=0,
    )
    lease = {"owner_id": "consumer", "lease_ref": "fixture"}
    pathlock = SimpleNamespace(
        pathlock_adopt=AsyncMock(return_value=lease),
        pathlock_release=AsyncMock(),
    )
    fs = SimpleNamespace(_async_agfs=pathlock)
    reenqueue = AsyncMock()
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(processor, "_reenqueue_semantic_msg", reenqueue)
    msg = _make_msg(
        context_type=context_type,
        telemetry_id=str(uuid4()),
        lock_handoff={"owner_id": "producer"},
    )
    worker = asyncio.create_task(processor.on_dequeue(msg.to_dict()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 1)
        pathlock.pathlock_adopt.assert_awaited_once_with(msg.lock_handoff)
        pathlock.pathlock_release.assert_awaited_once_with(lease)
        reenqueue.assert_not_awaited()
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        get_request_wait_tracker().cleanup(msg.telemetry_id)


@pytest.mark.asyncio
async def test_root_semantic_message_is_acknowledged_without_processing():
    processor = _processor()

    result = await processor.on_dequeue(
        _build_data(_make_msg(uri="viking://", context_type="resource"))
    )

    assert result.outcome is ProcessOutcome.SUCCESS
    assert result.value is None
    assert result.error is None


@pytest.mark.asyncio
async def test_memory_empty_dir_still_returns_success():
    """An empty memory directory must return a successful processing result."""
    processor = _processor()

    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=True)
    fake_fs.ls = AsyncMock(return_value=[])

    msg = _make_msg()
    data = _build_data(msg)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_work.resolve_telemetry",
            return_value=None,
        ),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.SUCCESS
    assert result.value is None
    assert result.error is None


@pytest.mark.asyncio
async def test_memory_ls_error_returns_failed():
    """A permanent filesystem error must return a failed processing result.

    Uses a real classify_api_error (no mock): FileNotFoundError is classified
    as permanent by the real classifier, so the processor returns FAILED.
    """
    processor = _processor()

    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=True)
    fake_fs.ls = AsyncMock(side_effect=FileNotFoundError("/memories not found"))

    msg = _make_msg()
    data = _build_data(msg)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_work.resolve_telemetry",
            return_value=None,
        ),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.value is None
    assert result.error == (f"Failed to list memory directory {msg.uri}: /memories not found")


@pytest.mark.asyncio
async def test_memory_ls_error_after_execution_fails_without_requeue():
    """Storage failures after execution starts must not replay completed model work."""
    processor = _processor()

    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=True)
    fake_fs.ls = AsyncMock(side_effect=RuntimeError("500 Internal Server Error"))

    msg = _make_msg(telemetry_id="tel-1")
    data = _build_data(msg)

    reenqueue_mock = AsyncMock()

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_work.resolve_telemetry",
            return_value=None,
        ),
        patch.object(processor, "_reenqueue_semantic_msg", new=reenqueue_mock),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.value is None
    assert "500 Internal Server Error" in result.error
    reenqueue_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_write_error_returns_failed():
    """An abstract/overview PermissionError must return a failed processing result.

    Exercises the write failure path with real classify_api_error: PermissionError
    is classified as permanent, so the processor returns FAILED.
    """
    processor = _processor()

    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=True)
    fake_fs.ls = AsyncMock(return_value=[{"name": "file1.md", "isDir": False}])
    fake_fs.read_file = AsyncMock(return_value="some content")
    fake_fs.write_file = AsyncMock(side_effect=PermissionError("Permission denied"))
    fake_fs._async_agfs.pathlock_acquire_exact_batch = AsyncMock(return_value={"lease_ref": "test"})
    fake_fs._async_agfs.pathlock_release = AsyncMock()
    fake_fs._uri_to_path = MagicMock(
        side_effect=lambda uri, ctx=None: f"/local/acc1/{uri.removeprefix('viking://')}"
    )

    msg = _make_msg(skip_vectorization=True)
    data = _build_data(msg)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_work.resolve_telemetry",
            return_value=None,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            return_value=SimpleNamespace(
                semantic=SimpleNamespace(
                    overview_max_chars=100_000,
                    abstract_max_chars=10_000,
                )
            ),
        ),
        patch.object(
            processor,
            "_generate_single_file_summary",
            new=AsyncMock(return_value={"name": "file1.md", "summary": "test summary"}),
        ),
        patch.object(
            processor,
            "_generate_overview",
            new=AsyncMock(return_value="# Overview\ntest overview"),
        ),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.FAILED
    assert result.value is None
    assert result.error == (f"Failed to write abstract/overview for {msg.uri}: Permission denied")
