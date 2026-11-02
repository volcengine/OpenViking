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
        viking_fs=SimpleNamespace(),
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
