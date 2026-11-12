# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for LockAcquisitionError propagation through write_context (#4612).

``VikingFS.write_context`` wrapped every internal failure into a bare
``IOError`` without ``from e``, destroying the exception type before the
semantic queue's lock-retry guards could see it: the
``except LockAcquisitionError: raise`` guards added by #4615 in
``semantic_processor``/``semantic_executor`` never matched, lock conflicts
fell into the generic transient branch that records circuit-breaker
failures, and ``classify_api_error`` (which only inspects ``__cause__``)
saw ``unknown``.

The tests drive the real mixin methods with a minimal fake ``_async_agfs``;
no LLM, storage, or local config is involved.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.viking_fs._access import _AccessMixin
from openviking.storage.viking_fs._ops import _OpsMixin
from openviking.storage.viking_fs._semantic import _SemanticMixin
from openviking_cli.session.user_id import UserIdentifier

LOCK_ERROR_TEXT = "internal error: encrypted write lock error: lock acquire timed out after 1001ms"

DIR_URI = "viking://user/alice/memories/events"


def _ctx():
    return RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)


class _FakeAGFS:
    """Minimal ``_async_agfs`` double with controllable mkdir/write failures."""

    def __init__(self, write_error=None, mkdir_error=None):
        self.write_error = write_error
        self.mkdir_error = mkdir_error
        self.write_paths = []
        self.mkdir_paths = []

    async def ensure_parent_dirs(self, path, fs_ctx=None, **kwargs):
        del path, fs_ctx, kwargs
        return None

    async def mkdir(self, path, fs_ctx=None, **kwargs):
        del fs_ctx, kwargs
        self.mkdir_paths.append(path)
        if self.mkdir_error is not None:
            raise self.mkdir_error
        return None

    async def write(self, path, content, fs_ctx=None, auto_pathlock=True, **kwargs):
        del content, fs_ctx, auto_pathlock, kwargs
        self.write_paths.append(path)
        if self.write_error is not None:
            raise self.write_error
        return None


class _WriteContextHarness(_AccessMixin, _OpsMixin, _SemanticMixin):
    """Expose the real mixin methods over a fake ``_async_agfs``."""

    def __init__(self, agfs):
        self._async_agfs = agfs

    async def _ensure_access(self, uri, ctx, action=None):
        del uri, ctx, action

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return "/local/acme/" + uri.removeprefix("viking://user/alice/")


async def _write_context(agfs, **kwargs):
    harness = _WriteContextHarness(agfs)
    options = {"content": "content body", "abstract": "abstract body", "overview": "overview"}
    options.update(kwargs)
    await harness.write_context(DIR_URI, ctx=_ctx(), **options)


@pytest.mark.asyncio
async def test_write_context_preserves_lock_acquisition_error_from_write():
    """A lock error on any internal write must keep its type (#4612)."""
    agfs = _FakeAGFS(write_error=LockAcquisitionError(LOCK_ERROR_TEXT))

    with pytest.raises(LockAcquisitionError, match="lock acquire timed out after 1001ms"):
        await _write_context(agfs)


@pytest.mark.asyncio
async def test_write_context_preserves_lock_acquisition_error_from_mkdir():
    """A lock error while creating the directory itself must keep its type."""
    agfs = _FakeAGFS(mkdir_error=LockAcquisitionError(LOCK_ERROR_TEXT))

    with pytest.raises(LockAcquisitionError, match="lock acquire timed out after 1001ms"):
        await _write_context(agfs)


@pytest.mark.asyncio
async def test_write_context_chains_non_lock_errors_with_cause():
    """Non-lock failures still surface as IOError, but chained via ``from e``.

    ``classify_api_error`` walks ``__cause__`` only (not ``__context__``), so
    the bare ``raise IOError(...)`` lost the original error for every caller.
    """
    original = PermissionError("permission denied")
    agfs = _FakeAGFS(write_error=original)

    with pytest.raises(IOError) as excinfo:
        await _write_context(agfs)

    assert excinfo.value.__cause__ is original


def _processor():
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=SimpleNamespace()))
    return SemanticProcessor(vlm_resolver=resolver)


def _make_msg(uri=DIR_URI, context_type="memory", **kwargs):
    defaults = {
        "id": "test-msg-4612",
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


def _lock_contention_fs():
    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=True)
    fake_fs.ls = AsyncMock(return_value=[{"name": "file1.md", "isDir": False}])
    fake_fs.read_file = AsyncMock(return_value="some content")
    fake_fs.write_file = AsyncMock(side_effect=LockAcquisitionError(LOCK_ERROR_TEXT))
    fake_fs._async_agfs.pathlock_acquire_exact_batch = AsyncMock(return_value={"lease_ref": "test"})
    fake_fs._async_agfs.pathlock_release = AsyncMock()
    fake_fs._uri_to_path = MagicMock(
        side_effect=lambda uri, ctx=None: f"/local/acc1/{uri.removeprefix('viking://')}"
    )
    return fake_fs


@pytest.mark.asyncio
async def test_process_memory_directory_reraises_lock_error_unwrapped():
    """The #4615 guard must see LockAcquisitionError, not a wrapped surrogate.

    Pins the consumer contract that write_context's IOError wrapping defeated
    for errors flowing through wrapped write paths.
    """
    processor = _processor()
    fake_fs = _lock_contention_fs()
    msg = _make_msg(skip_vectorization=True)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fake_fs,
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
        with pytest.raises(LockAcquisitionError, match="lock acquire timed out"):
            await processor._process_memory_directory(msg, ctx=_ctx())


@pytest.mark.asyncio
async def test_lock_error_requeues_without_breaker_failure():
    """Top-level handling must take the lock-specific requeue, not the
    transient branch that records a circuit-breaker failure."""
    processor = _processor()
    breaker = SimpleNamespace(
        check=lambda: None,
        record_failure=MagicMock(),
        record_success=MagicMock(),
        retry_after=0,
    )
    processor._circuit_breakers["acc1"] = breaker
    requeue_mock = AsyncMock(return_value=ProcessResult.requeued())
    fake_fs = _lock_contention_fs()
    msg = _make_msg(skip_vectorization=True, telemetry_id="tel-4612")

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
        patch.object(processor, "_requeue_semantic_msg_after_error", new=requeue_mock),
    ):
        result = await processor.on_dequeue(msg.to_dict())

    assert result.outcome is ProcessOutcome.REQUEUED
    requeue_mock.assert_awaited_once()
    breaker.record_failure.assert_not_called()
