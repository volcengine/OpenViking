"""Errors must survive the queue/task boundary without losing their public details."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.responses import response_from_result
from openviking.service.resource_service import ResourceService
from openviking.service.task_domain import TaskRecord, TaskStatus, sanitize_task_result
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
@pytest.mark.parametrize("raised", [False, True])
async def test_failed_resource_preserves_details_through_wait_and_http(monkeypatch, raised):
    monkeypatch.setattr("openviking.service.resource_service.is_git_repo_url", lambda _: False)
    monkeypatch.setattr("openviking.connector.routing.is_git_repo_url", lambda _: False)
    details = {"source": "https://example.com/doc", "upstream_status_code": 403}
    task = TaskRecord(task_id="failure-test", task_type="add_resource")

    async def fail(task_id, error, **kwargs):
        task.status = TaskStatus.FAILED
        task.error = error
        task.result = sanitize_task_result(kwargs["result"])

    tracker = SimpleNamespace(
        create=AsyncMock(return_value=task),
        start=AsyncMock(),
        get_task_auth=AsyncMock(return_value={}),
        fail=AsyncMock(side_effect=fail),
        wait=AsyncMock(return_value=task),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker", lambda: tracker
    )
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    service = ResourceService(
        viking_fs=SimpleNamespace(_async_agfs=SimpleNamespace()),
        resource_processor=SimpleNamespace(),
        skill_processor=object(),
    )
    service.execute_add_resource_job = AsyncMock(
        side_effect=OpenVikingError("upstream forbidden", code="PERMISSION_DENIED", details=details)
        if raised
        else None,
        return_value={
            "status": "error",
            "errors": ["upstream forbidden"],
            "code": "PERMISSION_DENIED",
            "details": details,
        },
    )
    processor = AddResourceProcessor(service, "add_resource", service._viking_fs)
    msg = AddResourceMsg(
        task_id=task.task_id,
        root_uri="viking://resources/doc",
        account_id="acct",
        user_id="alice",
        role="user",
    )
    await processor._process(msg, {})
    tracker.fail.assert_awaited_once()
    assert task.result["details"] == details

    service._prepare_standard_source_plan = AsyncMock(return_value=object())
    service._enqueue_source_plan = AsyncMock(return_value={"task_id": task.task_id})
    result = await service.add_resource(
        path=details["source"],
        to=msg.root_uri,
        wait=True,
        ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )
    response = response_from_result(result)
    assert response.status_code == 403
    assert json.loads(response.body)["error"] == {
        "code": "PERMISSION_DENIED",
        "message": "upstream forbidden",
        "details": details,
    }


@pytest.mark.asyncio
async def test_failed_resource_wait_exposes_queue_work_errors(monkeypatch):
    """Embedding/semantic queue errors keep uri/brief in the wait=true envelope."""
    monkeypatch.setattr("openviking.service.resource_service.is_git_repo_url", lambda _: False)
    monkeypatch.setattr("openviking.connector.routing.is_git_repo_url", lambda _: False)

    work_error = {
        "message": "Failed to generate embedding: image dimensions are too small",
        "uri": "viking://resources/matrix/pixel.png",
        "err_brief": "The content could not be processed; it may be corrupted or unsupported.",
    }
    task = TaskRecord(task_id="queue-error-test", task_type="add_resource")
    task.status = TaskStatus.FAILED
    task.error = work_error["message"]
    task.result = {"queue_status": {"Embedding": {"error_count": 1, "errors": [work_error]}}}

    tracker = SimpleNamespace(wait=AsyncMock(return_value=task))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)

    service = ResourceService(
        viking_fs=SimpleNamespace(),
        resource_processor=SimpleNamespace(),
        skill_processor=object(),
    )
    service._prepare_standard_source_plan = AsyncMock(return_value=object())
    service._enqueue_source_plan = AsyncMock(return_value={"task_id": task.task_id})

    result = await service.add_resource(
        path="https://example.com/pixel.png",
        to="viking://resources/matrix",
        wait=True,
        ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    assert result["status"] == "error"
    assert result["details"]["errors"] == [work_error]

    response = response_from_result(result)
    body = json.loads(response.body)
    assert body["error"]["code"] == "PROCESSING_ERROR"
    assert body["error"]["details"]["errors"] == [work_error]


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_resource_completeness_survives_queue_wait_and_http(
    monkeypatch, watch_vfs, watch_tracker, complete
):
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker", lambda: watch_tracker
    )
    result = {
        "status": "success" if complete else "partial",
        "root_uri": "viking://resources/docs",
        "meta": {"resolved_head_sha": "revision"},
    }
    if not complete:
        result.update(
            import_complete=False,
            failed_files=[{"path": "unreadable.md", "error": "temporary read failure"}],
        )
    service = ResourceService(
        viking_fs=watch_vfs, resource_processor=object(), skill_processor=object()
    )
    service.execute_add_resource_job = AsyncMock(return_value=result)
    processor = AddResourceProcessor(service, "add_resource", watch_vfs)
    processor._record_watch_execution = AsyncMock()
    msg = AddResourceMsg(
        task_id="completeness-test",
        root_uri=result["root_uri"],
        account_id="default",
        user_id="default",
        role="user",
        watch_task_id="completeness-watch",
    )
    await processor._process(msg, {})
    task = await watch_tracker.get(msg.task_id, "default", "default")
    assert task.status is TaskStatus.COMPLETED
    assert task.result["status"] == result["status"]
    assert ("source_checkpoint" in task.result) is complete
    if not complete:
        assert task.result["import_complete"] is False
        assert task.result["failed_files"] == result["failed_files"]

    service._prepare_standard_source_plan = AsyncMock(return_value=object())
    service._enqueue_source_plan = AsyncMock(return_value={"task_id": msg.task_id})
    waited = await service.add_resource(
        path="https://example.com/docs",
        to=msg.root_uri,
        wait=True,
        ctx=RequestContext(user=UserIdentifier("default", "default"), role=Role.USER),
    )
    response = response_from_result(waited)
    assert response["status"] == "ok"
    assert response["result"]["status"] == result["status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("local_deny", [False, True])
async def test_source_preflight_denial_uses_persisted_watch_binding(
    monkeypatch, watch_vfs, local_deny
):
    from openviking.utils.resource_processor import ResourceProcessor
    from openviking_cli.exceptions import PermissionDeniedError
    from tests.misc.test_resource_processor_mv import _revocation_evidence

    root = "viking://resources/docs"
    source = "https://example.feishu.cn/wiki/source"
    ctx = RequestContext(user=UserIdentifier("default", "default"), role=Role.USER)
    await watch_vfs.mkdir(root, exist_ok=True, ctx=ctx)
    await watch_vfs.write_file(root + "/old.md", "must stay", ctx=ctx)
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: watch_vfs)
    processor = ResourceProcessor(vikingdb=object(), media_storage=None)
    watch = SimpleNamespace(
        path="https://example.feishu.cn/wiki/different",
        last_status=None,
        last_task_id=None,
    )
    manager = SimpleNamespace(get_task_by_uri=AsyncMock(return_value=watch))
    service = ResourceService(
        viking_fs=watch_vfs,
        resource_processor=processor,
        skill_processor=object(),
        watch_manager=manager,
    )
    error = (
        PermissionDeniedError("local network denied")
        if local_deny
        else OpenVikingError(
            "upstream denied",
            code="PERMISSION_DENIED",
            details=_revocation_evidence(source)[0]["error_details"],
        )
    )
    service._prepare_standard_source_plan = AsyncMock(side_effect=error)
    with pytest.raises(OpenVikingError):
        await service.add_resource(source, ctx=ctx, to=root)
    assert await watch_vfs.read_file(root + "/old.md", ctx=ctx) == "must stay"
    if local_deny:
        manager.get_task_by_uri.assert_not_awaited()
    else:
        manager.get_task_by_uri.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("successful", [False, True])
async def test_watch_source_binding_requires_completed_import_not_registration(successful):
    root, source = "viking://resources/docs", "https://example.feishu.cn/wiki/source"
    watch = SimpleNamespace(
        path=source,
        last_status="completed" if successful else None,
        last_task_id="previous" if successful else None,
    )
    record = TaskRecord(
        task_id="previous",
        task_type="add_resource",
        status=TaskStatus.COMPLETED,
        meta={"source_path": source},
        result={"root_uri": root},
    )
    manager = SimpleNamespace(
        get_task_by_uri=AsyncMock(return_value=watch),
        tracker=SimpleNamespace(get=AsyncMock(return_value=record)),
    )
    service = ResourceService(watch_manager=manager)
    ctx = RequestContext(user=UserIdentifier("default", "default"), role=Role.USER)
    assert await service._read_watch_source_binding(root, ctx) == (source if successful else None)
    assert manager.tracker.get.await_count == int(successful)
