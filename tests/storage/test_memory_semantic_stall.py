# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory semantic queue stall fix (issue #864).

Ensures that _process_memory_directory() error paths propagate exceptions
so that on_dequeue() always calls report_success() or report_error().
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _make_msg(uri="viking://user/memories", context_type="memory", **kwargs):
    """Build a minimal SemanticMsg for testing."""
    defaults = {
        "id": "test-msg-1",
        "uri": uri,
        "context_type": context_type,
        "recursive": False,
        "role": "root",
        "account_id": "acc1",
        "user_id": "usr1",
        "agent_id": "test-agent",
        "telemetry_id": "",
        "target_uri": "",
        "lifecycle_lock_handle_id": "",
        "changes": None,
        "is_code_repo": False,
    }
    defaults.update(kwargs)
    return SemanticMsg.from_dict(defaults)


def _build_data(msg: SemanticMsg) -> dict:
    """Wrap a SemanticMsg into the dict format on_dequeue expects."""
    return msg.to_dict()


@pytest.mark.asyncio
async def test_memory_empty_dir_still_reports_success():
    """When viking_fs.ls returns an empty list, report_success() must be called."""
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(return_value=[])

    msg = _make_msg()
    data = _build_data(msg)

    success_called = False

    def on_success():
        nonlocal success_called
        success_called = True

    error_called = False

    def on_error(error_msg, error_data=None):
        nonlocal error_called
        error_called = True

    processor.set_callbacks(on_success, lambda: None, on_error)

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
        await processor.on_dequeue(data)

    assert success_called, "report_success() was not called for empty memory directory"
    assert not error_called, "report_error() should not be called for empty directory"


@pytest.mark.asyncio
async def test_memory_ls_error_reports_error():
    """When viking_fs.ls raises a filesystem error, report_error() must be called.

    Uses a real classify_api_error (no mock) — FileNotFoundError is classified
    as permanent by the real classifier, so the processor calls report_error().
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(side_effect=FileNotFoundError("/memories not found"))

    msg = _make_msg()
    data = _build_data(msg)

    success_called = False

    def on_success():
        nonlocal success_called
        success_called = True

    error_called = False
    error_info = {}

    def on_error(error_msg, error_data=None):
        nonlocal error_called, error_info
        error_called = True
        error_info["msg"] = error_msg

    processor.set_callbacks(on_success, lambda: None, on_error)

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
        await processor.on_dequeue(data)

    assert error_called, "report_error() was not called when ls() raised an exception"
    assert not success_called, "report_success() should not be called on ls() error"
    assert "/memories not found" in error_info["msg"]


@pytest.mark.asyncio
async def test_memory_ls_transient_error_requeues():
    """Transient errors during ls() re-enqueue the msg and increment requeue count.

    A 500-class error wrapped by the processor's `raise RuntimeError(...) from e`
    is classified as `transient`. The outer on_dequeue() path must call
    _reenqueue_semantic_msg(), bump requeue_count, and fire both report_requeue()
    and report_success() — not report_error().
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(side_effect=RuntimeError("500 Internal Server Error"))

    msg = _make_msg(telemetry_id="tel-1")
    data = _build_data(msg)

    success_called = False
    requeue_called = False
    error_called = False

    def on_success():
        nonlocal success_called
        success_called = True

    def on_requeue():
        nonlocal requeue_called
        requeue_called = True

    def on_error(error_msg, error_data=None):
        nonlocal error_called
        error_called = True

    processor.set_callbacks(on_success, on_requeue, on_error)

    reenqueue_mock = AsyncMock()

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.resolve_telemetry",
            return_value=None,
        ),
        patch.object(processor, "_reenqueue_semantic_msg", new=reenqueue_mock),
    ):
        await processor.on_dequeue(data)

    assert requeue_called, "report_requeue() must fire for transient errors"
    assert success_called, "report_success() must fire after successful re-enqueue"
    assert not error_called, "report_error() must NOT fire for transient errors"
    reenqueue_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_memory_write_error_reports_error():
    """When abstract/overview write raises PermissionError, report_error() is called.

    Exercises the write failure path with real classify_api_error — PermissionError
    is classified as permanent, so the processor calls report_error().
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(return_value=[{"name": "file1.md", "isDir": False}])
    fake_fs.read_file = AsyncMock(return_value="some content")
    fake_fs.write_file = AsyncMock(side_effect=PermissionError("Permission denied"))

    msg = _make_msg()
    data = _build_data(msg)

    success_called = False

    def on_success():
        nonlocal success_called
        success_called = True

    error_called = False
    error_info = {}

    def on_error(error_msg, error_data=None):
        nonlocal error_called, error_info
        error_called = True
        error_info["msg"] = error_msg

    processor.set_callbacks(on_success, lambda: None, on_error)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.resolve_telemetry",
            return_value=None,
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
        await processor.on_dequeue(data)

    assert error_called, "report_error() was not called when write() raised PermissionError"
    assert not success_called, "report_success() should not be called on write error"
    assert "Permission denied" in error_info["msg"]
