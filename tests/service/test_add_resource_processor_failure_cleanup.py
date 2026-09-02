"""Regression tests for #4501: rollback empty reserved targets on add-resource failure."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import TaskStatus
from openviking.storage.queuefs import QueueManager
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )


@pytest.mark.asyncio
async def test_processor_error_cleans_reserved_target_when_flag_set(monkeypatch):
    """AddResourceProcessor must rollback newly reserved targets on business errors."""
    lock = {"lease_ref": "lock-1"}
    cleanup = AsyncMock(return_value=True)
    service = SimpleNamespace(
        execute_add_resource_job=AsyncMock(
            return_value={"status": "error", "errors": ["Parse error: No /Root object!"]}
        ),
        _cleanup_reserved_target_if_empty=cleanup,
        _link_resource_reason_memory=AsyncMock(),
    )
    task_tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        get_task_auth=AsyncMock(return_value={}),
        wait_for_descendants=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_adopt=AsyncMock(return_value=lock),
            pathlock_release=AsyncMock(),
        ),
        delete_temp=AsyncMock(),
    )
    processor = AddResourceProcessor(
        service,
        __import__("asyncio").get_running_loop(),
        QueueManager.ADD_RESOURCE,
        viking_fs,
    )
    msg = AddResourceMsg(
        task_id="task-4501",
        path="broken.pdf",
        root_uri="viking://resources/f5-repro/broken-pdf",
        account_id="account-1",
        user_id="user-1",
        role="user",
        lock_handoff={"ref": "handoff-1"},
        cleanup_empty_target_on_failure=True,
    )

    await processor._process(msg, msg.to_dict())

    task_tracker.fail.assert_awaited_once()
    cleanup.assert_awaited_once()
    call = cleanup.await_args.kwargs
    assert call["root_uri"] == "viking://resources/f5-repro/broken-pdf"
    assert call["resource_lock"] == lock


@pytest.mark.asyncio
async def test_processor_error_skips_cleanup_when_target_preexisting(monkeypatch):
    cleanup = AsyncMock(return_value=True)
    service = SimpleNamespace(
        execute_add_resource_job=AsyncMock(
            return_value={"status": "error", "errors": ["Parse error: boom"]}
        ),
        _cleanup_reserved_target_if_empty=cleanup,
        _link_resource_reason_memory=AsyncMock(),
    )
    task_tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        get_task_auth=AsyncMock(return_value={}),
        wait_for_descendants=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_adopt=AsyncMock(return_value={"lease_ref": "lock-1"}),
            pathlock_release=AsyncMock(),
        ),
        delete_temp=AsyncMock(),
    )
    processor = AddResourceProcessor(
        service,
        __import__("asyncio").get_running_loop(),
        QueueManager.ADD_RESOURCE,
        viking_fs,
    )
    msg = AddResourceMsg(
        task_id="task-4501b",
        path="broken.pdf",
        root_uri="viking://resources/existing/report",
        account_id="account-1",
        user_id="user-1",
        role="user",
        lock_handoff={"ref": "handoff-1"},
        cleanup_empty_target_on_failure=False,
    )

    await processor._process(msg, msg.to_dict())

    cleanup.assert_not_awaited()
