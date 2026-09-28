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
from openviking.utils.content_hash import content_md5
from openviking.utils.ingest_options import IngestOptions
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

    async def _ensure_access(self, uri, ctx, action):
        del uri, ctx, action


@pytest.mark.asyncio
async def test_content_write_stat_skips_directory_vector_count(ctx):
    fake_fs = _FakeVikingFS()
    fake_fs.stat = AsyncMock(return_value={"isDir": True})
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    assert await coordinator._safe_stat("viking://resources/demo", ctx=ctx) == {"isDir": True}
    fake_fs.stat.assert_awaited_once_with(
        "viking://resources/demo",
        ctx=ctx,
        skip_count=True,
    )


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
    assert vectorize_file.await_args.kwargs["file_md5"] == content_md5(b"updated")
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
        ingest_options=IngestOptions.from_search_tags(["team=search"], mode="append"),
    )

    written = sidecar_fs.write_file.await_args.args[1]
    assert parse_abstract_overview(written).body == "Updated body only.\n"
    assert parse_abstract_overview(written).metadata == parse_abstract_overview(current).metadata
    sidecar_coordinator._enqueue_semantic_refresh.assert_not_awaited()
    vectorize_directory.assert_awaited_once()
    assert vectorize_directory.await_args.kwargs["ingest_options"] == IngestOptions(
        search_tags=["team=search"], search_tag_mode="append"
    )
    assert sidecar_result["semantic_status"] == "skipped"
    assert sidecar_result["vector_status"] == "queued"


@pytest.mark.asyncio
async def test_direct_write_passes_final_md5_and_old_abstract_to_semantic_refresh(ctx):
    file_uri = "viking://resources/demo.py"

    class _VikingDB:
        async def get_l2_diff_records_by_uris(self, uris, *, ctx):
            assert uris == [file_uri]
            return {file_uri: {"md5": "old-md5", "abstract": "old abstract"}}

    fake_fs = _FakeVikingFS()
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs, vikingdb=_VikingDB())
    enqueue = AsyncMock(return_value=FreshnessAction.REFRESH_NOW)
    coordinator._enqueue_semantic_refresh = enqueue

    await coordinator._write_direct_with_refresh(
        uri=file_uri,
        root_uri="viking://resources",
        content="updated",
        mode="replace",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        written_bytes=7,
        telemetry_id="",
    )

    assert enqueue.await_args.kwargs["file_md5"] == content_md5(b"updated")
    assert enqueue.await_args.kwargs["file_abstract"] == "old abstract"


@pytest.mark.asyncio
async def test_semantic_message_carries_file_md5_and_old_abstract(monkeypatch, ctx):
    file_uri = "viking://resources/demo.py"
    queue = SimpleNamespace(enqueue=AsyncMock(return_value="enqueued"))
    manager = SimpleNamespace(SEMANTIC="Semantic", get_queue=lambda *args, **kwargs: queue)
    monkeypatch.setattr(content_write_module, "get_queue_manager", lambda: manager)
    monkeypatch.setattr(
        content_write_module,
        "plan_abstract_overview_refresh",
        AsyncMock(return_value=SimpleNamespace(action=FreshnessAction.REFRESH_NOW)),
    )
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())

    await coordinator._enqueue_semantic_refresh_changes(
        root_uri="viking://resources",
        context_type="resource",
        changes={"modified": [file_uri]},
        ctx=ctx,
        file_md5s={file_uri: "new-md5"},
        file_abstracts={file_uri: "old abstract"},
    )

    msg = queue.enqueue.await_args.args[0]
    assert msg.file_md5s == {file_uri: "new-md5"}
    assert msg.file_abstracts == {file_uri: "old abstract"}


@pytest.mark.asyncio
async def test_direct_append_passes_md5_of_final_content_without_vector_db(ctx):
    fake_fs = _FakeVikingFS()
    fake_fs.read_file.return_value = "previous"
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    enqueue = AsyncMock(return_value=FreshnessAction.REFRESH_NOW)
    coordinator._enqueue_semantic_refresh = enqueue

    await coordinator._write_direct_with_refresh(
        uri="viking://resources/demo.py",
        root_uri="viking://resources",
        content=" updated",
        mode="append",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        written_bytes=8,
        telemetry_id="",
    )

    assert enqueue.await_args.kwargs["file_md5"] == content_md5(b"previous updated")
    assert enqueue.await_args.kwargs["file_abstract"] == ""


@pytest.mark.asyncio
async def test_direct_write_continues_when_old_abstract_lookup_fails(ctx):
    class _FailingVikingDB:
        async def get_l2_diff_records_by_uris(self, uris, *, ctx):
            raise RuntimeError("vector lookup unavailable")

    fake_fs = _FakeVikingFS()
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs, vikingdb=_FailingVikingDB())
    enqueue = AsyncMock(return_value=FreshnessAction.REFRESH_NOW)
    coordinator._enqueue_semantic_refresh = enqueue

    await coordinator._write_direct_with_refresh(
        uri="viking://resources/demo.py",
        root_uri="viking://resources",
        content="updated",
        mode="replace",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        written_bytes=7,
        telemetry_id="",
    )

    assert enqueue.await_args.kwargs["file_abstract"] == ""
    assert enqueue.await_args.kwargs["file_md5"] == content_md5(b"updated")


@pytest.mark.asyncio
async def test_old_abstract_lookup_uses_viking_fs_vector_store_when_not_injected(ctx):
    file_uri = "viking://agent/skills/demo/SKILL.md"

    class _VectorStore:
        async def get_l2_diff_records_by_uris(self, uris, *, ctx):
            return {file_uri: {"abstract": "skill summary"}}

    fake_fs = _FakeVikingFS()
    fake_fs._get_vector_store = lambda: _VectorStore()
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)

    assert await coordinator._load_file_abstracts([file_uri], ctx=ctx) == {
        file_uri: "skill summary"
    }


@pytest.mark.asyncio
async def test_write_builds_ingest_options_before_scheduling_resource_refresh(ctx):
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())
    coordinator._safe_stat = AsyncMock(return_value={"isDir": False})
    coordinator._resolve_root_uri = AsyncMock(return_value="viking://resources")
    coordinator._write_direct_with_refresh = AsyncMock(
        return_value={"uri": "viking://resources/demo.md"}
    )

    await coordinator.write(
        uri="viking://resources/demo.md",
        content="updated",
        ctx=ctx,
        tags=["team=search"],
        tag_mode="append",
    )

    ingest_options = coordinator._write_direct_with_refresh.await_args.kwargs["ingest_options"]
    assert ingest_options.search_tags == ["team=search"]
    assert ingest_options.search_tag_mode == "append"


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
    coordinator._enqueue_semantic_refresh = AsyncMock(return_value=FreshnessAction.MARK_PENDING)

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
