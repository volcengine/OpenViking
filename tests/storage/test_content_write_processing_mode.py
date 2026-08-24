# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.storage import content_write as content_write_module
from openviking.storage.abstract_overview import (
    parse_abstract_overview,
    render_abstract_overview,
)
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
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


def _sidecar(level=ContextLevel.ABSTRACT, body="Original body."):
    return render_abstract_overview(
        level,
        "viking://resources/demo",
        body,
        {
            "generated_by": {"component": "test", "trigger": "test"},
            "freshness": {
                "total_entries": 1,
                "sampled_entries": 1,
                "unsampled_entries": 0,
                "pending_child_changes": 0,
            },
        },
    )


@pytest.fixture
def ctx():
    return RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)


@pytest.mark.asyncio
async def test_direct_write_skips_semantic_refresh_for_vectors_only_and_sidecar_body_edits(
    monkeypatch, ctx
):
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

    current = _sidecar()
    sidecar_fs = _FakeVikingFS()
    sidecar_fs.read_file.side_effect = [
        current,
        current,
        _sidecar(ContextLevel.OVERVIEW, "Overview."),
    ]
    vectorize_directory = AsyncMock()
    monkeypatch.setattr(content_write_module, "vectorize_directory_meta", vectorize_directory)
    sidecar_coordinator = ContentWriteCoordinator(viking_fs=sidecar_fs)
    sidecar_coordinator._enqueue_semantic_refresh = AsyncMock(
        side_effect=AssertionError("sidecar body writes must not regenerate semantics")
    )

    sidecar_result = await sidecar_coordinator._write_direct_with_refresh(
        uri="viking://resources/demo/.abstract.md",
        root_uri="viking://resources/demo",
        content="Updated body only.",
        mode="replace",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        written_bytes=len("Updated body only.".encode()),
        telemetry_id="",
    )

    written = sidecar_fs.write_file.await_args.args[1]
    assert parse_abstract_overview(written).body == "Updated body only.\n"
    assert parse_abstract_overview(written).metadata == parse_abstract_overview(current).metadata
    sidecar_coordinator._enqueue_semantic_refresh.assert_not_awaited()
    vectorize_directory.assert_awaited_once()
    assert sidecar_result["semantic_status"] == "skipped"
    assert sidecar_result["vector_status"] == "queued"


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
async def test_automatic_wide_directory_delay_reports_deferred(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._enqueue_semantic_refresh = AsyncMock(
        return_value=FreshnessAction.MARK_PENDING
    )

    result = await coordinator._write_direct_with_refresh(
        uri="viking://resources/wide/demo.md",
        root_uri="viking://resources/wide",
        content="updated",
        mode="replace",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        written_bytes=7,
        telemetry_id="",
    )

    assert result["semantic_status"] == "deferred"
    assert result["vector_status"] == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overview_refreshed", "expected_overview_status"),
    [(True, "complete"), (False, "skipped")],
)
async def test_memory_write_accepts_processing_mode_without_switching_refresh(
    monkeypatch, ctx, overview_refreshed, expected_overview_status
):
    fake_fs = _FakeVikingFS()
    monkeypatch.setattr(
        content_write_module.MemoryUpdater,
        "refresh_schema_overview",
        AsyncMock(return_value=overview_refreshed),
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
        uri="viking://user/user-1/memories/demo.md",
        root_uri="viking://user/user-1/memories",
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
    assert result["overview_status"] == expected_overview_status
