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

    processor.set_callbacks(on_success, on_error)

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
    """When viking_fs.ls raises, report_error() must be called (not stuck)."""
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(side_effect=OSError("disk read failed"))

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

    processor.set_callbacks(on_success, on_error)

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
            "openviking.storage.queuefs.semantic_processor.classify_api_error",
            return_value="permanent",
        ),
    ):
        await processor.on_dequeue(data)

    assert error_called, "report_error() was not called when ls() raised an exception"
    assert not success_called, "report_success() should not be called on ls() error"
    assert "disk read failed" in error_info["msg"]
