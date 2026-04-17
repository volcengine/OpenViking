# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for memory semantic queue stall fix.

_process_memory_directory() previously had silent early-return paths for ls
and write_file failures, which bypassed on_dequeue()'s completion callbacks
and left the queue's in_progress counter stuck. These tests pin the fixed
behaviour: error paths propagate and on_dequeue reports error (not success)
through the real classify_api_error path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _make_msg(**kwargs) -> SemanticMsg:
    defaults = {
        "uri": "viking://user/default/memories/preferences",
        "context_type": "memory",
        "recursive": False,
    }
    defaults.update(kwargs)
    return SemanticMsg(**defaults)


@pytest.mark.asyncio
async def test_ls_filesystem_error_reports_error(monkeypatch):
    """FileNotFoundError from ls must reach on_error (not on_success)."""
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(side_effect=FileNotFoundError("/memories"))

    success_called = False
    error_called = False

    def on_success():
        nonlocal success_called
        success_called = True

    def on_error(msg, data=None):
        nonlocal error_called
        error_called = True

    processor.set_callbacks(on_success, lambda: None, on_error)

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.resolve_telemetry",
        lambda _tid: None,
    )

    await processor.on_dequeue(_make_msg().to_dict())

    assert error_called, "FileNotFoundError should be classified permanent and reach on_error"
    assert not success_called, "on_success must NOT be called when ls fails"


@pytest.mark.asyncio
async def test_empty_memory_dir_reports_success(monkeypatch):
    """Empty directory still completes successfully — no regression."""
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.ls = AsyncMock(return_value=[])

    success_called = False

    def on_success():
        nonlocal success_called
        success_called = True

    processor.set_callbacks(on_success, lambda: None, lambda msg, data=None: None)

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: fake_fs,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.resolve_telemetry",
        lambda _tid: None,
    )

    await processor.on_dequeue(_make_msg().to_dict())

    assert success_called, "empty memory directory should still report success"
