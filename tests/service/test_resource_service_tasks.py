# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.service.task_tracker import (
    TaskStatus,
    TaskTracker,
    reset_task_tracker,
    set_task_tracker,
)
from openviking.storage.transaction import NO_LOCK
from openviking_cli.session.user_id import UserIdentifier

pytestmark = pytest.mark.asyncio


class _NoopTaskStore:
    async def create(self, task):
        return None

    async def update(self, task):
        return None

    async def get(self, task_id, *, account_id=None, user_id=None):
        return None

    async def list(self, account_id, *, user_id=None):
        return []

    async def delete(self, task_id, *, account_id, user_id=None):
        return None


@pytest.fixture(autouse=True)
def clean_task_tracker():
    reset_task_tracker()
    yield
    reset_task_tracker()


async def test_run_add_resource_task_fails_when_queue_status_has_errors(monkeypatch):
    tracker = TaskTracker(store=_NoopTaskStore())
    set_task_tracker(tracker)
    service = ResourceService()
    ctx = RequestContext(
        user=UserIdentifier("acme", "alice", "agent-1"),
        role=Role.ADMIN,
    )
    task = await tracker.create(
        "add_resource",
        resource_id="viking://resources/demo",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
    )
    cleanup_calls = []

    async def fake_add_resource(**kwargs):
        assert kwargs["wait"] is True
        return {
            "status": "success",
            "root_uri": "viking://resources/demo",
            "queue_status": {
                "Semantic": {
                    "processed": 0,
                    "requeue_count": 0,
                    "error_count": 1,
                    "errors": [{"message": "semantic processing failed"}],
                },
                "Embedding": {
                    "processed": 0,
                    "requeue_count": 0,
                    "error_count": 0,
                    "errors": [],
                },
            },
        }

    async def cleanup(success: bool):
        cleanup_calls.append(success)

    monkeypatch.setattr(service, "add_resource", fake_add_resource)

    await service._run_add_resource_task(
        task.task_id,
        ctx=ctx,
        add_kwargs={"path": "/tmp/demo.md", "ctx": ctx},
        resource_lock=NO_LOCK,
        source_cleanup=cleanup,
    )

    stored = await tracker.get(
        task.task_id,
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
    )
    assert stored is not None
    assert stored.status == TaskStatus.FAILED
    assert stored.result is None
    assert "queue processing failed" in stored.error
    assert "Semantic error_count=1" in stored.error
    assert "semantic processing failed" in stored.error
    assert cleanup_calls == [False]
