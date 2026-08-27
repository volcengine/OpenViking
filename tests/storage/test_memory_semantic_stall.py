# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory semantic queue stall fix (issue #864).

Ensures that _process_memory_directory() returns an explicit queue outcome on
every success, failure, and retry path.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.queuefs.queue_hook import ProcessOutcome
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


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


@pytest.mark.asyncio
async def test_root_semantic_message_is_acknowledged_without_processing():
    processor = SemanticProcessor()

    result = await processor.on_dequeue(
        _build_data(_make_msg(uri="viking://", context_type="resource"))
    )

    assert result.outcome is ProcessOutcome.SUCCESS


@pytest.mark.asyncio
async def test_memory_empty_dir_still_returns_success():
    """An empty memory directory is a completed queue outcome."""
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(return_value=[])

    msg = _make_msg()
    data = _build_data(msg)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.resolve_telemetry",
            return_value=None,
        ),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.SUCCESS


@pytest.mark.asyncio
async def test_memory_ls_error_returns_failure():
    """When viking_fs.ls raises a filesystem error, the result must be failed.

    Uses a real classify_api_error (no mock) — FileNotFoundError is classified
    as permanent by the real classifier.
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(side_effect=FileNotFoundError("/memories not found"))

    msg = _make_msg()
    data = _build_data(msg)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.resolve_telemetry",
            return_value=None,
        ),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.FAILED
    assert "/memories not found" in result.error


@pytest.mark.asyncio
async def test_memory_ls_transient_error_requeues():
    """Transient errors during ls() re-enqueue the msg and increment requeue count.

    A 500-class error wrapped by the processor's `raise RuntimeError(...) from e`
    is classified as `transient`, so the handler requests a queue-managed retry.
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(side_effect=RuntimeError("500 Internal Server Error"))

    msg = _make_msg(telemetry_id="tel-1")
    data = _build_data(msg)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.resolve_telemetry",
            return_value=None,
        ),
    ):
        result = await processor.on_dequeue(data)

    assert result.outcome is ProcessOutcome.REQUEUE
    assert result.retry_payload.id == msg.id
    assert "500 Internal Server Error" in result.error


@pytest.mark.asyncio
async def test_memory_write_error_returns_failure():
    """When abstract/overview write raises PermissionError, processing fails.

    Exercises the write failure path with real classify_api_error — PermissionError
    is classified as permanent.
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
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
            "openviking.storage.queuefs.semantic_processor.resolve_telemetry",
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
    assert "Permission denied" in result.error
