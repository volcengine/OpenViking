# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Same-source Watch recovery decisions for failed derived indexing."""

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.resource.watch_manager import (
    _SOURCE_RECOVERY_MAX_ATTEMPTS,
    WatchManager,
    _same_source_action,
)
from openviking.resource.watch_types import WatchRun, WatchRunResult, WatchTask
from openviking_cli.exceptions import PermissionDeniedError
from tests.utils.watch_fixtures import shape_watch

SHA = "a" * 40
CHECKPOINT = {"kind": "git", "head_sha": SHA, "instruction": ""}


def _task(
    *,
    status: str | None = "failed",
    attempts: int = 0,
    checkpoint: dict | None = None,
    error: str | None = "embedding Work failed",
) -> WatchTask:
    return WatchTask.from_dict(
        {
            "path": "https://github.com/example/repo.git",
            "to_uri": "viking://resources/example",
            "source_checkpoint": dict(CHECKPOINT if checkpoint is None else checkpoint),
            "last_status": status,
            "last_error": error,
            "last_task_id": "failed-import",
            "recovery_attempts": attempts,
        }
    )


def _failed(
    checkpoint: dict | None = None,
    previous_status: str | None = "failed",
    previous_attempts: int = 0,
):
    return WatchManager._result(
        "failed",
        {
            "import_complete": True,
            "source_checkpoint": dict(CHECKPOINT if checkpoint is None else checkpoint),
        },
        "embedding Work failed",
        connector=False,
        instruction="",
        previous_checkpoint=dict(CHECKPOINT if checkpoint is None else checkpoint),
        previous_status=previous_status,
        previous_attempts=previous_attempts,
    )


def test_failed_same_source_is_recovered_not_skipped():
    assert _same_source_action(CHECKPOINT, _task(status="failed")) == "refresh"


def test_completed_same_source_still_skips():
    assert _same_source_action(CHECKPOINT, _task(status="completed")) == "skip"


def test_legacy_completed_without_status_still_skips():
    assert _same_source_action(CHECKPOINT, _task(status=None)) == "skip"


def test_recovery_attempts_are_bounded():
    assert (
        _same_source_action(
            CHECKPOINT, _task(status="failed", attempts=_SOURCE_RECOVERY_MAX_ATTEMPTS)
        )
        == "exhausted"
    )


def test_new_source_always_refreshes():
    new_sha = {"kind": "git", "head_sha": "b" * 40, "instruction": ""}
    assert _same_source_action(new_sha, _task(status="failed", attempts=99)) == "refresh"


def test_first_failure_does_not_count_as_a_recovery_attempt():
    result = _failed(previous_status="completed")
    assert result.status == "failed"
    assert result.recovery_attempts == 0


def test_failed_recovery_increments_attempts():
    result = _failed(previous_status="failed")
    assert result.recovery_attempts == 1


def test_success_clears_attempts_and_keeps_plain_checkpoint():
    result = WatchManager._result(
        "completed",
        {
            "import_complete": True,
            "source_checkpoint": {**CHECKPOINT, "recovery_attempts": 1},
        },
        None,
        connector=False,
        instruction="",
        previous_checkpoint={**CHECKPOINT, "recovery_attempts": 1},
        previous_status="failed",
    )
    assert result.source_checkpoint == CHECKPOINT
    assert result.recovery_attempts == 0


def test_source_failure_without_checkpoint_counts_an_attempt():
    result = WatchManager._result(
        "failed",
        {"import_complete": False},
        "clone failed",
        connector=False,
        instruction="",
        previous_checkpoint=dict(CHECKPOINT),
        previous_status="failed",
        previous_attempts=1,
    )
    assert result.source_checkpoint is None
    assert result.recovery_attempts == 2


def test_new_source_failure_resets_the_budget():
    new_checkpoint = {"kind": "git", "head_sha": "b" * 40, "instruction": ""}
    result = WatchManager._result(
        "failed",
        {"import_complete": True, "source_checkpoint": new_checkpoint},
        "embedding Work failed",
        connector=False,
        instruction="",
        previous_checkpoint=dict(CHECKPOINT),
        previous_status="failed",
        previous_attempts=_SOURCE_RECOVERY_MAX_ATTEMPTS,
    )
    assert result.recovery_attempts == 0


def test_import_failure_keeps_no_checkpoint():
    result = WatchManager._result(
        "failed",
        {"import_complete": False},
        "clone failed",
        connector=False,
        instruction="",
        previous_checkpoint=None,
        previous_status=None,
    )
    assert result.source_checkpoint is None
    assert result.recovery_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, attempts, outcome",
    [
        ("failed", 0, "refresh"),
        ("completed", 0, "completed"),
        ("failed", _SOURCE_RECOVERY_MAX_ATTEMPTS, "failed"),
    ],
)
async def test_same_source_submission(
    status, attempts, outcome, watch_store, watch_vfs, monkeypatch
):
    refresh = AsyncMock(return_value={"status": "success", "task_id": "new"})
    manager = WatchManager(
        watch_store, viking_fs=watch_vfs, resource_service=SimpleNamespace(refresh_resource=refresh)
    )
    monkeypatch.setattr(manager, "_probe_git_checkpoint", AsyncMock(return_value=CHECKPOINT))
    task = _task(status=status, attempts=attempts)
    result = await manager._submit_resource(WatchRun("watch", None, task), AsyncMock())
    if outcome == "refresh":
        assert result == {"status": "success", "task_id": "new"}
        refresh.assert_awaited_once()
    else:
        assert result.status == outcome
        refresh.assert_not_awaited()
        if outcome == "failed":
            assert (
                result.error
                == "Git Watch automatic recovery attempts exhausted: embedding Work failed"
            )


def test_legacy_record_defaults_to_zero_attempts():
    data = _task(status=None).to_storage_dict()
    del data["recovery_attempts"]
    del data["last_status"]
    legacy = WatchTask.from_dict(data)
    assert legacy.recovery_attempts == 0
    assert _same_source_action(CHECKPOINT, legacy) == "skip"


@pytest.mark.asyncio
async def test_manual_trigger_bypasses_exhaustion(watch_store, watch_vfs, monkeypatch):
    refresh = AsyncMock(
        return_value=WatchRunResult(
            status="completed", source_checkpoint=CHECKPOINT, recovery_attempts=0
        )
    )
    manager = WatchManager(
        watch_store, viking_fs=watch_vfs, resource_service=SimpleNamespace(refresh_resource=refresh)
    )
    monkeypatch.setattr(manager, "_probe_git_checkpoint", AsyncMock(return_value=CHECKPOINT))
    target = "viking://resources/example"
    task = _task(status="failed", attempts=_SOURCE_RECOVERY_MAX_ATTEMPTS)
    task.to_uri = target
    task.next_execution_time = datetime.now() - timedelta(seconds=1)
    await watch_vfs.mkdir(target, ctx=manager.context(task), exist_ok=True)
    await watch_store.create_task(task)

    await manager.trigger(task.task_id, manual=True)
    refresh.assert_awaited_once()
    saved = await watch_store.get_task(task.task_id)
    assert saved.last_status == "completed"
    assert saved.recovery_attempts == 0

    await shape_watch(watch_store, task.task_id, next_run_at=time.time() - 1)
    refresh.reset_mock()
    await manager.trigger(task.task_id, manual=False)
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_pre_task_failure_counts_and_exhausts(watch_store, watch_vfs, monkeypatch):
    refresh = AsyncMock(side_effect=RuntimeError("refresh blew up"))
    manager = WatchManager(
        watch_store, viking_fs=watch_vfs, resource_service=SimpleNamespace(refresh_resource=refresh)
    )
    monkeypatch.setattr(manager, "_probe_git_checkpoint", AsyncMock(return_value=CHECKPOINT))
    target = "viking://resources/example"
    task = _task(status="failed", attempts=0)
    task.to_uri = target
    task.next_execution_time = datetime.now() - timedelta(seconds=1)
    await watch_vfs.mkdir(target, ctx=manager.context(task), exist_ok=True)
    await watch_store.create_task(task)

    runs = await manager.due(1)
    assert runs
    with pytest.raises(RuntimeError, match="refresh blew up"):
        await manager.execute(runs[0])
    saved = await watch_store.get_task(task.task_id)
    assert saved.last_status == "failed"
    assert saved.recovery_attempts == 1

    await shape_watch(watch_store, task.task_id, next_run_at=time.time() - 1)
    runs = await manager.due(1)
    assert runs
    with pytest.raises(RuntimeError, match="refresh blew up"):
        await manager.execute(runs[0])
    saved = await watch_store.get_task(task.task_id)
    assert saved.recovery_attempts == 2

    await shape_watch(watch_store, task.task_id, next_run_at=time.time() - 1)
    runs = await manager.due(1)
    assert runs
    assert await manager.execute(runs[0]) is None
    assert refresh.await_count == 2
    saved = await watch_store.get_task(task.task_id)
    assert saved.last_status == "failed"
    assert "recovery attempts exhausted" in (saved.last_error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_type", [PermissionDeniedError, RuntimeError])
async def test_pre_task_exception_type_and_message_are_preserved(
    exc_type, watch_store, watch_vfs, monkeypatch
):
    refresh = AsyncMock(side_effect=exc_type("upstream refused"))
    manager = WatchManager(
        watch_store, viking_fs=watch_vfs, resource_service=SimpleNamespace(refresh_resource=refresh)
    )
    monkeypatch.setattr(manager, "_probe_git_checkpoint", AsyncMock(return_value=CHECKPOINT))
    target = "viking://resources/example"
    task = _task(status="failed", attempts=0)
    task.to_uri = target
    await watch_vfs.mkdir(target, ctx=manager.context(task), exist_ok=True)
    await watch_store.create_task(task)

    with pytest.raises(exc_type, match="upstream refused") as raised:
        await manager.trigger(task.task_id, manual=True)
    assert str(raised.value) == "upstream refused"
    saved = await watch_store.get_task(task.task_id)
    assert saved.last_error == "upstream refused"


@pytest.mark.asyncio
async def test_pre_task_cancellation_is_not_counted(watch_store, watch_vfs, monkeypatch):
    refresh = AsyncMock(side_effect=asyncio.CancelledError())
    manager = WatchManager(
        watch_store, viking_fs=watch_vfs, resource_service=SimpleNamespace(refresh_resource=refresh)
    )
    monkeypatch.setattr(manager, "_probe_git_checkpoint", AsyncMock(return_value=CHECKPOINT))
    target = "viking://resources/example"
    task = _task(status="failed", attempts=0)
    task.to_uri = target
    await watch_vfs.mkdir(target, ctx=manager.context(task), exist_ok=True)
    await watch_store.create_task(task)

    with pytest.raises(asyncio.CancelledError):
        await manager.trigger(task.task_id, manual=True)
    saved = await watch_store.get_task(task.task_id)
    assert saved.recovery_attempts == 0


@pytest.mark.asyncio
async def test_trigger_route_uses_manual_trigger(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking.server.routers import watches
    from openviking_cli.session.user_id import UserIdentifier

    task = _task(status="failed", attempts=_SOURCE_RECOVERY_MAX_ATTEMPTS)
    triggered: list[bool] = []

    class FakeManager:
        async def get_task(self, *args, **kwargs):
            return task

        async def trigger(self, task_id, *, manual=True):
            triggered.append(manual)
            return None

    monkeypatch.setattr(watches, "_wm", lambda: FakeManager())
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await watches.trigger_watch_by_id(task.task_id, None, ctx)
    assert triggered == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_type,expected_status",
    [(PermissionDeniedError, 403), (RuntimeError, 500)],
)
async def test_manual_trigger_http_error_shape(
    exc_type, expected_status, watch_store, watch_vfs, monkeypatch
):
    import httpx

    from openviking.server.app import create_app
    from openviking.server.config import ServerConfig
    from openviking.server.identity import RequestContext, Role
    from openviking.server.routers import watches
    from openviking_cli.session.user_id import UserIdentifier

    refresh = AsyncMock(side_effect=exc_type("upstream refused"))
    manager = WatchManager(
        watch_store, viking_fs=watch_vfs, resource_service=SimpleNamespace(refresh_resource=refresh)
    )
    monkeypatch.setattr(manager, "_probe_git_checkpoint", AsyncMock(return_value=CHECKPOINT))
    target = "viking://resources/example"
    task = _task(status="failed", attempts=0)
    task.to_uri = target
    await watch_vfs.mkdir(target, ctx=manager.context(task), exist_ok=True)
    await watch_store.create_task(task)

    class RouteManager:
        async def get_task(self, *args, **kwargs):
            return await manager.get_task(*args, **kwargs)

        async def trigger(self, task_id, *, manual=True):
            return await manager.trigger(task_id, manual=manual)

    monkeypatch.setattr(watches, "_wm", lambda: RouteManager())
    app = create_app(config=ServerConfig())
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    app.dependency_overrides[watches.get_request_context] = lambda: ctx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/watches/{task.task_id}/trigger")
    assert response.status_code == expected_status
    if exc_type is PermissionDeniedError:
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"
        assert response.json()["error"]["message"] == "upstream refused"
    else:
        assert response.json()["error"]["code"] == "INTERNAL"


@pytest.mark.asyncio
async def test_manual_trigger_api_bypasses_exhaustion(watch_store, watch_vfs, monkeypatch):
    import httpx

    from openviking.server.app import create_app
    from openviking.server.config import ServerConfig
    from openviking.server.identity import RequestContext, Role
    from openviking.server.routers import watches
    from openviking_cli.session.user_id import UserIdentifier

    refresh = AsyncMock(
        return_value=WatchRunResult(
            status="completed", source_checkpoint=CHECKPOINT, recovery_attempts=0
        )
    )
    manager = WatchManager(
        watch_store, viking_fs=watch_vfs, resource_service=SimpleNamespace(refresh_resource=refresh)
    )
    monkeypatch.setattr(manager, "_probe_git_checkpoint", AsyncMock(return_value=CHECKPOINT))
    target = "viking://resources/example"
    task = _task(status="failed", attempts=_SOURCE_RECOVERY_MAX_ATTEMPTS)
    task.to_uri = target
    await watch_vfs.mkdir(target, ctx=manager.context(task), exist_ok=True)
    await watch_store.create_task(task)

    class RouteManager:
        async def get_task(self, *args, **kwargs):
            return await manager.get_task(*args, **kwargs)

        async def trigger(self, task_id, *, manual=True):
            return await manager.trigger(task_id, manual=manual)

    monkeypatch.setattr(watches, "_wm", lambda: RouteManager())
    app = create_app(config=ServerConfig())
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    app.dependency_overrides[watches.get_request_context] = lambda: ctx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/watches/{task.task_id}/trigger")
    assert response.status_code == 200
    refresh.assert_awaited_once()
    saved = await watch_store.get_task(task.task_id)
    assert saved.last_status == "completed"
    assert saved.recovery_attempts == 0
