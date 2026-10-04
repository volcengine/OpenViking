# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage import content_write as content_write_module
from openviking.storage.abstract_overview import (
    render_abstract_overview,
)
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.queuefs.semantic_ops.freshness_policy import FreshnessAction
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
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
async def test_create_routes_as_replace_without_prelock_target_stat(ctx):
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())
    coordinator._safe_stat = AsyncMock(return_value={"not_found": True})
    coordinator._resolve_root_uri = AsyncMock(return_value="viking://resources")
    coordinator._write_direct_with_refresh = AsyncMock(
        return_value={"uri": "viking://resources/new.md"}
    )

    await coordinator.write(
        uri="viking://resources/new.md",
        content="new content",
        mode="create",
        ctx=ctx,
    )

    kwargs = coordinator._write_direct_with_refresh.await_args.kwargs
    assert kwargs["mode"] == "replace"
    coordinator._safe_stat.assert_not_awaited()
    coordinator._resolve_root_uri.assert_awaited_once_with(
        "viking://resources/new.md",
        ctx=ctx,
        _allow_not_found=True,
        anchor_to_parent=True,
        validate_storage=False,
    )


@pytest.mark.asyncio
async def test_direct_write_reads_target_once_after_lock_and_reuses_formal_state(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    events = []
    original_acquire = fake_fs._async_agfs.pathlock_acquire_exact

    async def _acquire(path):
        events.append("lock")
        return await original_acquire(path)

    async def _stat(*args, **kwargs):
        del args, kwargs
        events.append("stat")
        return {"isDir": False}

    fake_fs._async_agfs.pathlock_acquire_exact = _acquire
    fake_fs.stat = _stat
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    captured = {}

    async def _snapshot(**kwargs):
        captured["formal_snapshot"] = kwargs["formal_snapshot"]
        captured["target_preexisting"] = kwargs["target_preexisting"]
        return SimpleNamespace()

    async def _plan(**kwargs):
        del kwargs
        return None, SimpleNamespace()

    async def _commit(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(
            semantic_requested=False, vector_requested=False, semantic_action=None
        )

    monkeypatch.setattr(content_write_module, "build_rnfv_snapshot", _snapshot)
    monkeypatch.setattr(content_write_module, "build_context_update_plan_from_snapshot", _plan)
    monkeypatch.setattr(content_write_module, "commit_and_enqueue_plan", _commit)

    await coordinator._write_direct_with_refresh(
        uri="viking://resources/demo.md",
        root_uri="viking://resources",
        content="updated",
        mode="replace",
        response_mode="replace",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        telemetry_id="",
        ingest_options=IngestOptions(),
    )

    assert events == ["lock", "stat"]
    assert captured["target_preexisting"] is True
    assert captured["formal_snapshot"] == (
        {"": content_write_module.FormalEntry(is_dir=False)},
        True,
    )


@pytest.mark.asyncio
async def test_missing_append_starts_from_empty_content(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    fake_fs.stat = AsyncMock(side_effect=NotFoundError("viking://resources/new.md", "file"))
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    captured = {}

    async def _snapshot(**kwargs):
        captured["bytes"] = kwargs["store"]._data
        return SimpleNamespace()

    async def _plan(**kwargs):
        del kwargs
        return None, SimpleNamespace()

    async def _commit(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(
            semantic_requested=False, vector_requested=False, semantic_action=None
        )

    monkeypatch.setattr(content_write_module, "build_rnfv_snapshot", _snapshot)
    monkeypatch.setattr(content_write_module, "build_context_update_plan_from_snapshot", _plan)
    monkeypatch.setattr(content_write_module, "commit_and_enqueue_plan", _commit)

    result = await coordinator._write_direct_with_refresh(
        uri="viking://resources/new.md",
        root_uri="viking://resources",
        content="first line",
        mode="append",
        response_mode="append",
        context_type="resource",
        wait=False,
        timeout=None,
        ctx=ctx,
        telemetry_id="",
        ingest_options=IngestOptions(),
    )

    fake_fs.read_file.assert_not_awaited()
    assert captured["bytes"] == b"first line"
    assert result["mode"] == "append"


def test_write_result_keeps_content_updated_compatibility_field():
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())

    result = coordinator._build_write_result(
        uri="viking://resources/demo.md",
        root_uri="viking://resources",
        context_type="resource",
        mode="replace",
        written_bytes=7,
        queue_status=None,
        semantic_status="skipped",
        vector_status="skipped",
    )

    assert result["content_updated"] is True


@pytest.mark.asyncio
async def test_missing_generated_sidecar_is_rejected_after_exact_lock(monkeypatch, ctx):
    fake_fs = _FakeVikingFS()
    events = []
    original_acquire = fake_fs._async_agfs.pathlock_acquire_exact

    async def _acquire(path):
        events.append("lock")
        return await original_acquire(path)

    fake_fs._async_agfs.pathlock_acquire_exact = _acquire
    coordinator = ContentWriteCoordinator(viking_fs=fake_fs)
    coordinator._classify_locked_write_target = AsyncMock(
        side_effect=InvalidArgumentError("cannot create generated abstract overview directly")
    )

    with pytest.raises(InvalidArgumentError, match="cannot create generated"):
        await coordinator.write(
            uri="viking://resources/demo/.abstract.md",
            content="manual body",
            mode="replace",
            ctx=ctx,
        )

    assert events == ["lock"]
    coordinator._classify_locked_write_target.assert_awaited_once()


def test_new_memory_file_renders_from_replace_mode_and_explicit_new_file_state():
    uri = "viking://user/account-1/memories/new.md"
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())

    rendered = coordinator._render_final_bytes(
        uri,
        "new memory content",
        mode="replace",
        existing_raw=None,
        is_new_file=True,
    )

    assert MemoryFileUtils.read(rendered.decode("utf-8"), uri=uri).content == "new memory content"


def test_plan_statuses_report_deferred_parent_and_queued_vector_work():
    coordinator = ContentWriteCoordinator(viking_fs=_FakeVikingFS())
    work = SimpleNamespace(
        semantic_requested=False,
        vector_requested=True,
        semantic_action=FreshnessAction.MARK_PENDING.value,
    )

    assert coordinator._plan_statuses(work, wait=False, queue_status=None) == (
        "deferred",
        "queued",
    )
