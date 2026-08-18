# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage import content_write as content_write_module
from openviking.storage.content_write import ContentWriteCoordinator
from openviking_cli.session.user_id import UserIdentifier


class _FakePathLock:
    """Mock for _async_agfs pathlock operations."""

    def __init__(self):
        self._lease = SimpleNamespace(id="lock-1")
        self.release_calls = []

    async def pathlock_acquire_exact(self, lock_path):
        del lock_path
        return self._lease

    async def pathlock_release(self, lease):
        self.release_calls.append(lease.id)


class _FakeVikingFS:
    def __init__(self):
        self.write_file = AsyncMock()
        self.read_file = AsyncMock(return_value="previous")
        self._async_agfs = _FakePathLock()

    def _uri_to_path(self, uri, ctx=None):
        return f"/fake/{uri}"


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)


@pytest.mark.asyncio
async def test_vectors_only_write_skips_semantic_refresh_and_vectorizes_file(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    vectorize_file = AsyncMock(return_value=True)
    semantic_refresh = AsyncMock(side_effect=AssertionError("semantic refresh should not run"))
    monkeypatch.setattr(content_write_module, "vectorize_file", vectorize_file, raising=False)
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._enqueue_semantic_refresh = semantic_refresh

    result = await coordinator._write_direct_with_refresh(
        uri="viking://resources/demo.md",
        root_uri="viking://resources",
        content="updated",
        mode="replace",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        written_bytes=7,
        telemetry_id="",
        processing_mode="vectors_only",
    )

    semantic_refresh.assert_not_awaited()
    vectorize_file.assert_awaited_once()
    assert vectorize_file.await_args.kwargs["file_path"] == "viking://resources/demo.md"
    assert vectorize_file.await_args.kwargs["parent_uri"] == "viking://resources"
    assert vectorize_file.await_args.kwargs["summary_dict"] == {
        "name": "demo.md",
        "summary": "",
    }
    assert "register_request_wait" not in vectorize_file.await_args.kwargs
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "queued"


@pytest.mark.asyncio
async def test_vectors_only_write_wait_reports_embedding_status(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    queue_status = {
        "Embedding": {"processed": 1, "error_count": 0, "errors": []},
        "Semantic": {"processed": 0, "error_count": 0, "errors": []},
    }
    monkeypatch.setattr(
        content_write_module, "vectorize_file", AsyncMock(return_value=True), raising=False
    )
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._wait_for_request = AsyncMock(return_value=queue_status)

    result = await coordinator._write_direct_with_refresh(
        uri="viking://resources/demo.md",
        root_uri="viking://resources",
        content="updated",
        mode="replace",
        context_type="resource",
        wait=True,
        timeout=3.0,
        ctx=ctx,
        written_bytes=7,
        telemetry_id="tm-test",
        processing_mode="vectors_only",
    )

    assert result["queue_status"] == queue_status
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "complete"


@pytest.mark.asyncio
async def test_vectors_only_write_wait_reports_skipped_when_nothing_enqueued(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        content_write_module, "vectorize_file", AsyncMock(return_value=False), raising=False
    )
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._wait_for_request = AsyncMock(return_value=None)

    result = await coordinator._write_direct_with_refresh(
        uri="viking://resources/obsolete.md",
        root_uri="viking://resources",
        content="",
        mode="replace",
        context_type="resource",
        wait=True,
        timeout=3.0,
        ctx=ctx,
        written_bytes=0,
        telemetry_id="tm-test",
        processing_mode="vectors_only",
    )

    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "skipped"


@pytest.mark.asyncio
async def test_memory_write_accepts_processing_mode_without_switching_refresh(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        content_write_module.MemoryUpdater,
        "refresh_schema_overview",
        AsyncMock(),
    )
    monkeypatch.setattr(
        content_write_module.MemoryUpdater,
        "refresh_file_embedding",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        content_write_module.MemoryUpdater,
        "memory_type_from_uri",
        lambda uri: "user",
    )
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._write_in_place = AsyncMock()

    result = await coordinator._write_memory_with_refresh(
        uri="viking://user/memories/demo.md",
        root_uri="viking://user/memories",
        content="updated",
        mode="replace",
        wait=True,
        timeout=3.0,
        ctx=ctx,
        written_bytes=7,
        telemetry_id="tm-test",
        processing_mode="vectors_only",
    )

    content_write_module.MemoryUpdater.refresh_schema_overview.assert_awaited_once()
    content_write_module.MemoryUpdater.refresh_file_embedding.assert_awaited_once()
    assert result["context_type"] == "memory"
    assert result["semantic_status"] == "skipped"
    assert result["overview_status"] == "complete"
