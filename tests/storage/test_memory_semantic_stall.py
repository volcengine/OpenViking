# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory semantic queue stall (#864) and poison-loop (#2734) fixes.

#864: _process_memory_directory() error paths must propagate so on_dequeue()
always calls report_success() or report_error() (never stalls).
#2734: a context_type="memory" message whose URI is a file or a vanished
directory is a terminal skip (report_success, no re-enqueue), while transient
stat()/ls() errors still requeue.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


class _NoopLockContext:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


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
async def test_memory_empty_dir_still_reports_success():
    """When viking_fs.ls returns an empty list, report_success() must be called."""
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.stat = AsyncMock(return_value={"isDir": True})
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
async def test_memory_non_directory_uri_skips_terminally():
    """A context_type='memory' msg whose URI is a file is skipped, not re-enqueued (issue #2734).

    A memory file reindexed with mode=semantic_and_vectors enqueues a
    context_type="memory" message whose URI is a file. stat() reports isDir=False,
    so the guard marks the message done and reports success without ever calling
    ls() — instead of ls()'ing the file, raising, and re-enqueuing forever (the
    AGFS poison loop). Directory-ness is decided by stat(), not by the ls() error,
    because ls() (_ls_original) collapses every failure into NotFoundError and
    therefore cannot distinguish a file/missing target from a transient error.
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.stat = AsyncMock(return_value={"name": "android-dual-host.md", "isDir": False})
    fake_fs.ls = AsyncMock(return_value=[])

    msg = _make_msg(uri="viking://user/usr1/memories/handoffs/active/proj/android-dual-host.md")
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

    assert success_called, "a non-directory memory URI must report success (terminal skip)"
    assert not requeue_called, "a non-directory memory URI must NOT be re-enqueued (no poison loop)"
    assert not error_called, "a non-directory memory URI must not report an error"
    fake_fs.ls.assert_not_called()


@pytest.mark.asyncio
async def test_memory_missing_directory_skips_terminally():
    """A vanished memory directory (stat raises not-found) is a terminal skip.

    If the directory disappeared between enqueue and processing there is nothing to
    summarize, so the message is acked rather than re-enqueued. Transient stat()
    errors are NOT swallowed here — they requeue (see
    test_memory_stat_transient_error_requeues).
    """
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.stat = AsyncMock(side_effect=FileNotFoundError("gone"))
    fake_fs.ls = AsyncMock(return_value=[])

    msg = _make_msg()
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

    assert success_called, "a vanished memory directory must report success (terminal skip)"
    assert not requeue_called, "a vanished memory directory must NOT be re-enqueued"
    assert not error_called, "a vanished memory directory must not report an error"
    fake_fs.ls.assert_not_called()


@pytest.mark.asyncio
async def test_memory_stat_transient_error_requeues():
    """Transient stat() errors re-enqueue before ls() is attempted."""
    processor = SemanticProcessor()

    fake_fs = MagicMock()
    fake_fs.stat = AsyncMock(side_effect=RuntimeError("500 Internal Server Error"))
    fake_fs.ls = AsyncMock(return_value=[])

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

    assert requeue_called, "report_requeue() must fire for transient stat() errors"
    assert success_called, "report_success() must fire after successful re-enqueue"
    assert not error_called, "report_error() must NOT fire for transient stat() errors"
    reenqueue_mock.assert_awaited_once()
    fake_fs.ls.assert_not_called()


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
    # stat reports a real directory, so the guard proceeds to ls(); the transient
    # ls() error must still requeue (it must NOT be collapsed into a terminal skip).
    fake_fs.stat = AsyncMock(return_value={"isDir": True})
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
    fake_fs.stat = AsyncMock(return_value={"isDir": True})
    fake_fs.ls = AsyncMock(return_value=[{"name": "file1.md", "isDir": False}])
    fake_fs.read_file = AsyncMock(return_value="some content")
    fake_fs.write_file = AsyncMock(side_effect=PermissionError("Permission denied"))
    fake_fs._uri_to_path = MagicMock(
        side_effect=lambda uri, ctx=None: f"/local/acc1/{uri.removeprefix('viking://')}"
    )

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
        patch("openviking.storage.transaction.LockContext", _NoopLockContext),
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
