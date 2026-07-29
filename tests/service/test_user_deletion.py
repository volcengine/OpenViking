# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from openviking.service.task_tracker import TaskStatus
from openviking.service.user_deletion import UserDeletionProcessor


async def test_cancel_user_tasks_waits_for_already_cancelling_work(monkeypatch):
    task = SimpleNamespace(
        task_id="task-1",
        task_type="add_resource",
        status=TaskStatus.CANCELLING,
    )
    tracker = SimpleNamespace(
        list_tasks=AsyncMock(side_effect=[[task], []]),
        cancel=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.service.user_deletion.get_task_tracker",
        Mock(return_value=tracker),
    )
    processor = UserDeletionProcessor(
        service=SimpleNamespace(),
        manager=SimpleNamespace(),
        service_loop=None,
        shared_upload_prefix="viking://upload",
    )

    await processor._cancel_user_tasks("acme", "alice")

    tracker.cancel.assert_awaited_once_with(
        "task-1",
        account_id="acme",
        user_id="alice",
    )
