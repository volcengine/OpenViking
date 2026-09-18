# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.storage.errors import ResourceBusyError
from openviking.utils import resource_processor as resource_processor_module
from openviking.utils.resource_processor import ResourceProcessor


class _FakeVikingFS:
    def __init__(self, existing=()):
        self.existing = set(existing)

    async def _ensure_access(self, uri, ctx, *, action):
        return None

    async def exists(self, uri, *, ctx):
        return uri in self.existing

    def _uri_to_path(self, uri, *, ctx):
        return f"/agfs/{uri}"


def _make_processor(monkeypatch, *, existing=()):
    processor = ResourceProcessor.__new__(ResourceProcessor)
    viking_fs = _FakeVikingFS(existing)
    monkeypatch.setattr(resource_processor_module, "get_viking_fs", lambda: viking_fs)
    return processor


@pytest.mark.asyncio
async def test_reservation_exhaustion_reports_retryable_lock_contention(monkeypatch):
    processor = _make_processor(monkeypatch)
    processor.acquire_resource_lock = AsyncMock(
        side_effect=ResourceBusyError(
            "busy",
            uri="viking://resources/report",
            conflict_type="path_busy",
        )
    )

    with pytest.raises(ResourceBusyError) as exc_info:
        await processor.reserve_unique_candidate(
            candidate_uri="viking://resources/report",
            ctx=object(),
            max_attempts=2,
        )

    assert exc_info.value.uri == "viking://resources/report"
    assert exc_info.value.conflict_type == "auto_name_reservation_busy"
    assert exc_info.value.retryable is True
    assert "checking 3 candidates" in str(exc_info.value)
    assert processor.acquire_resource_lock.await_count == 3


@pytest.mark.asyncio
async def test_true_auto_name_exhaustion_remains_file_exists(monkeypatch):
    candidates = {
        "viking://resources/report",
        "viking://resources/report_1",
        "viking://resources/report_2",
    }
    processor = _make_processor(monkeypatch, existing=candidates)
    processor.acquire_resource_lock = AsyncMock()

    with pytest.raises(FileExistsError):
        await processor.reserve_unique_candidate(
            candidate_uri="viking://resources/report",
            ctx=object(),
            max_attempts=2,
        )

    processor.acquire_resource_lock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reservation_returns_first_available_lock(monkeypatch):
    processor = _make_processor(
        monkeypatch,
        existing={"viking://resources/report"},
    )
    lease = object()
    processor.acquire_resource_lock = AsyncMock(return_value=lease)

    uri, acquired = await processor.reserve_unique_candidate(
        candidate_uri="viking://resources/report",
        ctx=object(),
        max_attempts=2,
    )

    assert uri == "viking://resources/report_1"
    assert acquired is lease


@pytest.mark.asyncio
async def test_acquire_lock_forwards_wait_timeout_to_backend(monkeypatch):
    """Ingest into a busy directory must wait for the tree lock (#4337)."""
    from types import SimpleNamespace

    viking_fs = _FakeVikingFS()
    viking_fs._async_agfs = SimpleNamespace(
        pathlock_acquire_tree=AsyncMock(return_value={"lease_ref": "tree"}),
        pathlock_acquire_exact=AsyncMock(return_value={"lease_ref": "exact"}),
    )
    monkeypatch.setattr(resource_processor_module, "get_viking_fs", lambda: viking_fs)

    await ResourceProcessor.acquire_resource_lock(
        "/agfs/resources/docs", uri="viking://resources/docs", timeout=60.0
    )
    viking_fs._async_agfs.pathlock_acquire_tree.assert_awaited_once_with(
        "/agfs/resources/docs", timeout_secs=60.0
    )
    viking_fs._async_agfs.pathlock_acquire_exact.assert_not_awaited()

    await ResourceProcessor.acquire_resource_lock(
        "/agfs/resources/docs/file.md",
        uri="viking://resources/docs/file.md",
        timeout=60.0,
        root_is_file=True,
    )
    viking_fs._async_agfs.pathlock_acquire_exact.assert_awaited_once_with(
        "/agfs/resources/docs/file.md", timeout_secs=60.0
    )


@pytest.mark.asyncio
async def test_acquire_lock_still_maps_busy_after_wait(monkeypatch):
    """A lock that stays busy past the wait maps to a retryable busy error."""
    from types import SimpleNamespace

    from openviking.storage.errors import LockAcquisitionError

    viking_fs = _FakeVikingFS()
    viking_fs._async_agfs = SimpleNamespace(
        pathlock_acquire_tree=AsyncMock(
            side_effect=LockAcquisitionError("lock acquire timed out after 60000ms")
        ),
    )
    monkeypatch.setattr(resource_processor_module, "get_viking_fs", lambda: viking_fs)

    with pytest.raises(ResourceBusyError) as exc_info:
        await ResourceProcessor.acquire_resource_lock(
            "/agfs/resources/docs", uri="viking://resources/docs", timeout=60.0
        )
    assert exc_info.value.retryable is True
    assert exc_info.value.conflict_type == "path_busy"
