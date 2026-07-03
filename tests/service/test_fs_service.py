# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for file-system service coordination behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, *, rm_error=None):
        self.rm_calls = []
        self.mv_calls = []
        self.rm_error = rm_error

    async def rm(self, uri, recursive=False, ctx=None):
        self.rm_calls.append({"uri": uri, "recursive": recursive, "ctx": ctx})
        if self.rm_error:
            raise self.rm_error
        return {"estimated_deleted_count": 3}

    async def mv(self, from_uri, to_uri, ctx=None):
        self.mv_calls.append({"from_uri": from_uri, "to_uri": to_uri, "ctx": ctx})


class _FakeWatchManager:
    def __init__(self):
        self.plan_calls = []
        self.move_calls = []
        self.sync_calls = []
        self.deactivate_calls = []
        self.plan_error = None

    async def plan_move_tasks_under_uri_internal(self, from_uri, to_uri):
        self.plan_calls.append({"from_uri": from_uri, "to_uri": to_uri})
        if self.plan_error:
            raise self.plan_error
        return {}

    async def move_tasks_under_uri_internal(self, from_uri, to_uri):
        self.move_calls.append({"from_uri": from_uri, "to_uri": to_uri})
        return [SimpleNamespace(task_id="watch-1")]

    async def sync_tasks_with_resource_move_internal(
        self,
        from_uri,
        to_uri,
        move_resource,
        rollback_resource=None,
    ):
        self.sync_calls.append({"from_uri": from_uri, "to_uri": to_uri})
        if self.plan_error:
            raise self.plan_error
        await move_resource()
        return [SimpleNamespace(task_id="watch-1")]

    async def deactivate_tasks_under_uri_internal(self, uri):
        self.deactivate_calls.append(uri)
        return [SimpleNamespace(task_id="watch-1")]


class _FakeResourceMemoryLinkService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def before_resource_delete(self, *, ctx, resource_uri, recursive=False):
        self.calls.append({"ctx": ctx, "resource_uri": resource_uri, "recursive": recursive})
        return self.result


class _FakeWatchScheduler:
    def __init__(self, watch_manager):
        self.watch_manager = watch_manager


class _FakeWaitTracker:
    def __init__(self):
        self.registered_requests = []
        self.registered_roots = []
        self.wait_calls = []
        self.cleaned = []

    def register_request(self, telemetry_id):
        self.registered_requests.append(telemetry_id)

    def register_semantic_root(self, telemetry_id, semantic_msg_id):
        self.registered_roots.append(
            {
                "telemetry_id": telemetry_id,
                "semantic_msg_id": semantic_msg_id,
                "request_was_registered": telemetry_id in self.registered_requests,
            }
        )

    async def wait_for_request(self, telemetry_id, timeout=None):
        self.wait_calls.append((telemetry_id, timeout))

    def build_queue_status(self, telemetry_id):
        return {
            "Semantic": {"processed": 1, "error_count": 0, "errors": []},
            "Embedding": {"processed": 0, "error_count": 0, "errors": []},
        }

    def mark_semantic_failed(self, telemetry_id, semantic_msg_id, message):
        pass

    def cleanup(self, telemetry_id):
        self.cleaned.append(telemetry_id)


class _FakeQueueManager:
    SEMANTIC = "semantic"

    def __init__(self):
        self.messages = []

    def get_queue(self, name, allow_create=False):
        assert name == self.SEMANTIC
        assert allow_create is True
        return self

    async def enqueue(self, msg):
        self.messages.append(msg)


@pytest.fixture
def request_context():
    return RequestContext(
        user=UserIdentifier("default", "ryoma"),
        role=Role.USER,
    )


@pytest.mark.asyncio
async def test_resource_rm_enqueues_parent_delete_refresh_and_waits(request_context):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    service._enqueue_delete_refresh = AsyncMock()
    service._wait_for_refresh = AsyncMock(return_value={"Semantic": {"pending_count": 0}})

    uri = "viking://resources/images/2026/06/10/不二周助_jpeg"
    result = await service.rm(
        uri,
        ctx=request_context,
        recursive=True,
        wait=True,
        timeout=12.0,
    )

    assert viking_fs.rm_calls == [{"uri": uri, "recursive": True, "ctx": request_context}]
    service._enqueue_delete_refresh.assert_awaited_once_with(
        root_uri="viking://resources/images/2026/06/10",
        deleted_uri=uri,
        context_type="resource",
        ctx=request_context,
    )
    service._wait_for_refresh.assert_awaited_once_with(timeout=12.0)
    assert result["semantic_root_uri"] == "viking://resources/images/2026/06/10"
    assert result["semantic_status"] == "complete"
    assert result["queue_status"] == {"Semantic": {"pending_count": 0}}


@pytest.mark.asyncio
async def test_resource_rm_reports_failed_semantic_status_when_wait_queue_has_errors(
    request_context,
):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    service._enqueue_delete_refresh = AsyncMock()
    service._wait_for_refresh = AsyncMock(
        return_value={
            "Semantic": {
                "processed": 1,
                "error_count": 1,
                "errors": [{"message": "refresh failed"}],
            }
        }
    )

    result = await service.rm(
        "viking://resources/images/2026/06/10/不二周助_jpeg",
        ctx=request_context,
        recursive=True,
        wait=True,
    )

    assert result["semantic_status"] == "failed"


@pytest.mark.asyncio
async def test_resource_rm_without_wait_only_queues_refresh(request_context):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    service._enqueue_delete_refresh = AsyncMock()
    service._wait_for_refresh = AsyncMock()

    uri = "viking://resources/images/2026/06/10/不二周助_jpeg"
    result = await service.rm(uri, ctx=request_context, recursive=True)

    service._enqueue_delete_refresh.assert_awaited_once()
    service._wait_for_refresh.assert_not_awaited()
    assert result["semantic_status"] == "queued"


@pytest.mark.asyncio
async def test_resource_rm_deactivates_watch_tasks(request_context):
    viking_fs = _FakeVikingFS()
    watch_manager = _FakeWatchManager()
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(watch_manager),
    )
    service._enqueue_delete_refresh = AsyncMock()

    await service.rm("viking://resources/codeask/wiki", ctx=request_context, recursive=True)

    assert watch_manager.deactivate_calls == ["viking://resources/codeask/wiki"]


@pytest.mark.asyncio
async def test_resource_rm_does_not_deactivate_watch_task_control_uri(request_context):
    viking_fs = _FakeVikingFS()
    watch_manager = _FakeWatchManager()
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(watch_manager),
    )

    await service.rm("viking://resources/.watch_tasks.json", ctx=request_context)

    assert watch_manager.deactivate_calls == []


@pytest.mark.asyncio
async def test_resource_mv_plans_then_moves_then_rewrites_watch_tasks(request_context):
    viking_fs = _FakeVikingFS()
    watch_manager = _FakeWatchManager()
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(watch_manager),
    )

    await service.mv(
        "viking://resources/codeask/wiki",
        "viking://resources/codeask/wiki-renamed",
        ctx=request_context,
    )

    assert watch_manager.sync_calls == [
        {
            "from_uri": "viking://resources/codeask/wiki",
            "to_uri": "viking://resources/codeask/wiki-renamed",
        }
    ]
    assert viking_fs.mv_calls == [
        {
            "from_uri": "viking://resources/codeask/wiki",
            "to_uri": "viking://resources/codeask/wiki-renamed",
            "ctx": request_context,
        }
    ]
    assert watch_manager.plan_calls == []
    assert watch_manager.move_calls == []


@pytest.mark.asyncio
async def test_resource_mv_conflict_fails_before_resource_move(request_context):
    viking_fs = _FakeVikingFS()
    watch_manager = _FakeWatchManager()
    watch_manager.plan_error = RuntimeError("watch conflict")
    service = FSService(
        viking_fs=viking_fs,
        watch_scheduler=_FakeWatchScheduler(watch_manager),
    )

    with pytest.raises(RuntimeError, match="watch conflict"):
        await service.mv(
            "viking://resources/codeask/wiki",
            "viking://resources/codeask/wiki-renamed",
            ctx=request_context,
        )

    assert viking_fs.mv_calls == []
    assert watch_manager.move_calls == []


@pytest.mark.asyncio
async def test_resource_mv_without_watch_scheduler_moves_resource_directly(request_context):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)

    await service.mv(
        "viking://resources/codeask/wiki",
        "viking://resources/codeask/wiki-renamed",
        ctx=request_context,
    )

    assert viking_fs.mv_calls == [
        {
            "from_uri": "viking://resources/codeask/wiki",
            "to_uri": "viking://resources/codeask/wiki-renamed",
            "ctx": request_context,
        }
    ]


@pytest.mark.asyncio
async def test_resource_rm_wait_registers_request_before_semantic_root(
    request_context,
    monkeypatch,
):
    viking_fs = _FakeVikingFS()
    service = FSService(viking_fs=viking_fs)
    tracker = _FakeWaitTracker()
    queue_manager = _FakeQueueManager()

    monkeypatch.setattr(
        "openviking.service.fs_service.get_current_telemetry",
        lambda: SimpleNamespace(telemetry_id="tm-fs-rm"),
    )
    monkeypatch.setattr(
        "openviking.service.fs_service.get_request_wait_tracker",
        lambda: tracker,
    )
    monkeypatch.setattr(
        "openviking.service.fs_service.get_queue_manager",
        lambda: queue_manager,
    )

    result = await service.rm(
        "viking://resources/images/2026/06/10/不二周助_jpeg",
        ctx=request_context,
        recursive=True,
        wait=True,
        timeout=3,
    )

    assert tracker.registered_requests == ["tm-fs-rm"]
    assert tracker.registered_roots
    assert tracker.registered_roots[0]["request_was_registered"] is True
    assert tracker.wait_calls == [("tm-fs-rm", 3)]
    assert tracker.cleaned == ["tm-fs-rm"]
    assert result["semantic_status"] == "complete"


@pytest.mark.asyncio
async def test_resource_rm_does_not_cleanup_memory_if_resource_delete_fails(request_context):
    delete_error = RuntimeError("delete failed")
    viking_fs = _FakeVikingFS(rm_error=delete_error)
    cleanup = {
        "status": "success",
        "memory_uris": ["viking://user/ryoma/memories/entities/动漫角色/越前龙马.md"],
    }
    link_service = _FakeResourceMemoryLinkService(cleanup)
    service = FSService(
        viking_fs=viking_fs,
        resource_memory_link_service=link_service,
    )

    with pytest.raises(RuntimeError, match="delete failed"):
        await service.rm(
            "viking://resources/images/2026/06/10/yueqian_jpeg",
            ctx=request_context,
            recursive=True,
        )

    assert link_service.calls == []


@pytest.mark.asyncio
async def test_resource_rm_refreshes_memory_overview_for_cleaned_memories(
    request_context,
    monkeypatch,
):
    cleanup = {
        "status": "success",
        "memory_uris": ["viking://user/ryoma/memories/entities/动漫角色/不二周助-write-test.md"],
        "deleted_memory_uris": [
            "viking://user/ryoma/memories/entities/动漫角色/不二周助-link-test2.md"
        ],
    }
    viking_fs = _FakeVikingFS()
    link_service = _FakeResourceMemoryLinkService(cleanup)
    service = FSService(
        viking_fs=viking_fs,
        resource_memory_link_service=link_service,
    )
    service._enqueue_delete_refresh = AsyncMock()

    refreshed = []

    async def fake_refresh_schema_overview(*, viking_fs, directory_uri, ctx):
        refreshed.append({"viking_fs": viking_fs, "directory_uri": directory_uri, "ctx": ctx})

    monkeypatch.setattr(
        "openviking.service.fs_service.MemoryUpdater.refresh_schema_overview",
        fake_refresh_schema_overview,
    )

    uri = "viking://resources/images/2026/06/11/不二周助_jpeg"
    result = await service.rm(uri, ctx=request_context, recursive=True)

    assert link_service.calls == [{"ctx": request_context, "resource_uri": uri, "recursive": True}]
    assert refreshed == [
        {
            "viking_fs": viking_fs,
            "directory_uri": "viking://user/ryoma/memories/entities/动漫角色",
            "ctx": request_context,
        }
    ]
    assert result["memory_cleanup"] == cleanup
