"""Errors must survive the queue/task boundary without losing their public details."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.responses import response_from_result
from openviking.service.resource_service import ResourceService
from openviking.service.task_domain import TaskRecord, TaskStatus
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["partial", "failed"])
async def test_resource_outcome_survives_task_wait_and_http(
    monkeypatch, watch_vfs, watch_tracker, outcome
):
    details = {"source": "https://example.com/doc", "upstream_status_code": 403}
    failures = [{"path": "unreadable.md", "error": "temporary read failure"}]
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker", lambda: watch_tracker
    )
    service = ResourceService(
        viking_fs=watch_vfs, resource_processor=object(), skill_processor=object()
    )
    service.execute_add_resource_job = AsyncMock(
        side_effect=OpenVikingError("upstream forbidden", code="PERMISSION_DENIED", details=details)
        if outcome == "failed"
        else None,
        return_value={
            "status": "partial",
            "root_uri": "viking://resources/docs",
            "meta": {"resolved_head_sha": "revision"},
            "import_complete": False,
            "failed_files": failures,
        },
    )
    processor = AddResourceProcessor(service, "add_resource", watch_vfs)
    msg = AddResourceMsg(
        task_id="outcome-test",
        root_uri="viking://resources/docs",
        account_id="acct",
        user_id="alice",
        role="user",
    )
    await processor._process(msg, {})
    task = await watch_tracker.get(msg.task_id, "acct", "alice")
    assert task.status is (TaskStatus.FAILED if outcome == "failed" else TaskStatus.COMPLETED)

    service._prepare_standard_source_plan = AsyncMock(return_value=object())
    service._enqueue_source_plan = AsyncMock(return_value={"task_id": msg.task_id})
    result = await service.add_resource(
        path=details["source"],
        to=msg.root_uri,
        wait=True,
        ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )
    response = response_from_result(result)
    if outcome == "failed":
        assert task.result["details"] == details
        assert response.status_code == 403
        assert json.loads(response.body)["error"] == {
            "code": "PERMISSION_DENIED",
            "message": "upstream forbidden",
            "details": details,
        }
    else:
        assert "source_checkpoint" not in task.result
        assert response["status"] == "ok"
        assert response["result"]["root_uri"] == msg.root_uri
        assert response["result"]["status"] == "partial"
        assert response["result"]["import_complete"] is False
        assert response["result"]["failed_files"] == failures


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
