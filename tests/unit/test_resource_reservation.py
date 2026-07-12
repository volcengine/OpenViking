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

    async def exists(self, uri, *, ctx):
        return uri in self.existing

    def _uri_to_path(self, uri, *, ctx):
        return f"/agfs/{uri}"


def _make_processor(monkeypatch, *, existing=()):
    processor = ResourceProcessor.__new__(ResourceProcessor)
    viking_fs = _FakeVikingFS(existing)
    monkeypatch.setattr(resource_processor_module, "get_viking_fs", lambda: viking_fs)
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: object(),
    )
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
