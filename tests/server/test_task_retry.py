# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Isolated unit tests for the server-owned task retry endpoint."""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import tasks as task_router
from openviking.service.task_tracker import TaskRecord, TaskStatus
from openviking_cli.exceptions import FailedPreconditionError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)


def _failed_task(*, error: str = "provider overloaded", attempt_number: int = 1) -> TaskRecord:
    return TaskRecord(
        task_id="failed-task",
        task_type="session_commit",
        status=TaskStatus.FAILED,
        resource_id="session-1",
        account_id="acme",
        user_id="alice",
        error=error,
        attempt_number=attempt_number,
    )


def test_task_response_only_exposes_owner_to_root_callers():
    task = _failed_task()

    regular_response = task_router._task_response(task, include_owner=False)
    root_response = task_router._task_response(task, include_owner=True)

    assert "owner_account_id" not in regular_response
    assert "owner_user_id" not in regular_response
    assert root_response["owner_account_id"] == "acme"
    assert root_response["owner_user_id"] == "alice"


class _Tracker:
    def __init__(self, task: TaskRecord):
        self.task = task
        self.link_calls: list[dict] = []
        self.resolve_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.resolved_task: TaskRecord | None = None

    async def get(self, task_id, **kwargs):
        self.get_calls.append({"task_id": task_id, **kwargs})
        return self.task if task_id == self.task.task_id else None

    async def find_active(self, *_args, **_kwargs):
        return None

    async def find_completed_operation(self, *_args, **_kwargs):
        return self.resolved_task

    async def link_retry(self, task_id, **kwargs):
        self.link_calls.append({"task_id": task_id, **kwargs})
        return TaskRecord(
            task_id=task_id,
            task_type=self.task.task_type,
            resource_id=self.task.resource_id,
            account_id="acme",
            user_id="alice",
            operation_id=self.task.operation_id,
            parent_task_id=self.task.task_id,
            attempt_number=self.task.attempt_number + 1,
        )

    async def resolve_failed(self, task_id, result, **kwargs):
        self.resolve_calls.append({"task_id": task_id, "result": result, **kwargs})
        self.task.status = TaskStatus.COMPLETED
        self.task.result = result
        self.task.error = None
        self.task.error_info = {}
        return self.task


class _Sessions:
    def __init__(self):
        self.calls = 0
        self.contexts: list[RequestContext] = []
        self.retry_state = {"state": "unavailable", "archive_uri": None}
        self.retry_result = {"task_id": "retry-task"}

    async def inspect_failed_commit(
        self,
        _session_id,
        _failed_task_id,
        _ctx,
        *,
        archive_uri=None,
        failed_task_created_at=None,
    ):
        self.contexts.append(_ctx)
        assert archive_uri is None
        assert failed_task_created_at is not None
        return self.retry_state

    async def retry_failed_commit(
        self,
        _session_id,
        _failed_task_id,
        _ctx,
        *,
        archive_uri=None,
        failed_task_created_at=None,
    ):
        self.calls += 1
        self.contexts.append(_ctx)
        assert archive_uri is None
        assert failed_task_created_at is not None
        return self.retry_result


class _Service:
    def __init__(self):
        self.sessions = _Sessions()


@pytest.mark.asyncio
async def test_retry_blocks_legacy_permanent_failure_before_starting_work(monkeypatch):
    tracker = _Tracker(_failed_task(error="token_expired"))
    service = _Service()
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)

    response = await task_router.retry_task("failed-task", task_router.RetryTaskRequest(), _ctx())

    assert response.result["disposition"] == "blocked"
    assert response.result["error"]["code"] == "AUTH_EXPIRED"
    assert service.sessions.calls == 0


@pytest.mark.asyncio
async def test_retry_reports_completed_legacy_archive_before_credential_prompt(monkeypatch):
    tracker = _Tracker(_failed_task(error="token_expired"))
    service = _Service()
    service.sessions.retry_state = {
        "state": "completed",
        "archive_uri": "viking://user/alice/sessions/session-1/history/archive_001",
    }
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)

    response = await task_router.retry_task("failed-task", task_router.RetryTaskRequest(), _ctx())

    assert response.result["disposition"] == "operation_resolved"
    assert response.result["resolution"] == "archive_complete"
    assert response.result["task_id"] == "failed-task"
    assert tracker.task.status == TaskStatus.COMPLETED
    assert service.sessions.calls == 0


@pytest.mark.asyncio
async def test_retry_creates_a_linked_attempt_and_returns_its_task_id(monkeypatch):
    tracker = _Tracker(_failed_task())
    service = _Service()
    service.sessions.retry_state = {"state": "failed_ready", "archive_uri": None}
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)

    response = await task_router.retry_task("failed-task", task_router.RetryTaskRequest(), _ctx())

    assert response.result["disposition"] == "accepted"
    assert response.result["task_id"] == "retry-task"
    assert response.result["attempt_number"] == 2
    assert tracker.link_calls == [
        {
            "task_id": "retry-task",
            "parent_task_id": "failed-task",
            "account_id": "acme",
            "user_id": "alice",
        }
    ]


@pytest.mark.asyncio
async def test_root_retry_uses_the_task_owner_context(monkeypatch):
    task = _failed_task()
    task.account_id = "tenant-b"
    task.user_id = "bob"
    tracker = _Tracker(task)
    service = _Service()
    service.sessions.retry_state = {"state": "failed_ready", "archive_uri": None}
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)
    root_ctx = RequestContext(
        user=UserIdentifier("system", "root"),
        role=Role.ROOT,
        actor_peer_id="peer-1",
        api_key="root-key",
    )

    response = await task_router.retry_task(
        "failed-task",
        task_router.RetryTaskRequest(
            owner_account_id="tenant-b",
            owner_user_id="bob",
        ),
        root_ctx,
    )

    assert response.result["disposition"] == "accepted"
    assert len(service.sessions.contexts) == 2
    for task_ctx in service.sessions.contexts:
        assert task_ctx.account_id == "tenant-b"
        assert task_ctx.user.user_id == "bob"
        assert task_ctx.role == Role.ROOT
        assert task_ctx.actor_peer_id == "peer-1"
        assert task_ctx.api_key == "root-key"


@pytest.mark.asyncio
async def test_retry_reports_no_action_when_service_starts_no_task(monkeypatch):
    tracker = _Tracker(_failed_task())
    service = _Service()
    service.sessions.retry_state = {"state": "failed_ready", "archive_uri": None}
    service.sessions.retry_result = {"reason": "nothing_to_retry"}
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)

    response = await task_router.retry_task("failed-task", task_router.RetryTaskRequest(), _ctx())

    assert response.result["disposition"] == "no_action"
    assert response.result["result"] == {"reason": "nothing_to_retry"}
    assert tracker.link_calls == []


@pytest.mark.asyncio
async def test_root_retry_requires_an_explicit_owner(monkeypatch):
    tracker = _Tracker(_failed_task())
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    root_ctx = RequestContext(
        user=UserIdentifier("system", "root"),
        role=Role.ROOT,
    )

    with pytest.raises(FailedPreconditionError, match="both owner_account_id and owner_user_id"):
        await task_router.retry_task("failed-task", task_router.RetryTaskRequest(), root_ctx)

    assert tracker.get_calls == []


@pytest.mark.asyncio
async def test_root_retry_accepts_an_explicit_system_owner(monkeypatch):
    task = _failed_task()
    task.account_id = task_router.SYSTEM_TASK_ACCOUNT_ID
    task.user_id = task_router.SYSTEM_TASK_USER_ID
    tracker = _Tracker(task)
    service = _Service()
    service.sessions.retry_state = {"state": "failed_ready", "archive_uri": None}
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)
    root_ctx = RequestContext(
        user=UserIdentifier("system", "root"),
        role=Role.ROOT,
    )

    response = await task_router.retry_task(
        "failed-task",
        task_router.RetryTaskRequest(
            owner_account_id=task_router.SYSTEM_TASK_ACCOUNT_ID,
            owner_user_id=task_router.SYSTEM_TASK_USER_ID,
        ),
        root_ctx,
    )

    assert response.result["disposition"] == "accepted"
    assert tracker.get_calls == [
        {
            "task_id": "failed-task",
            "account_id": task_router.SYSTEM_TASK_ACCOUNT_ID,
            "user_id": task_router.SYSTEM_TASK_USER_ID,
        }
    ]
    assert service.sessions.contexts == [root_ctx, root_ctx]


@pytest.mark.asyncio
async def test_root_retry_loads_persisted_task_with_explicit_owner(monkeypatch):
    task = _failed_task()
    task.account_id = "tenant-b"
    task.user_id = "bob"
    tracker = _Tracker(task)
    service = _Service()
    service.sessions.retry_state = {"state": "failed_ready", "archive_uri": None}

    async def load_from_store(task_id, **kwargs):
        tracker.get_calls.append({"task_id": task_id, **kwargs})
        if kwargs == {"account_id": "tenant-b", "user_id": "bob"}:
            return task
        return None

    tracker.get = load_from_store
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)
    root_ctx = RequestContext(
        user=UserIdentifier("system", "root"),
        role=Role.ROOT,
    )
    body = task_router.RetryTaskRequest(
        owner_account_id="tenant-b",
        owner_user_id="bob",
    )

    response = await task_router.retry_task("failed-task", body, root_ctx)

    assert response.result["disposition"] == "accepted"
    assert tracker.get_calls == [
        {
            "task_id": "failed-task",
            "account_id": "tenant-b",
            "user_id": "bob",
        }
    ]


@pytest.mark.asyncio
async def test_root_retry_rejects_an_incomplete_owner(monkeypatch):
    tracker = _Tracker(_failed_task())
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    root_ctx = RequestContext(
        user=UserIdentifier("system", "root"),
        role=Role.ROOT,
    )

    with pytest.raises(FailedPreconditionError, match="both owner_account_id and owner_user_id"):
        await task_router.retry_task(
            "failed-task",
            task_router.RetryTaskRequest(owner_account_id="tenant-b"),
            root_ctx,
        )

    assert tracker.get_calls == []


@pytest.mark.asyncio
async def test_non_root_retry_rejects_an_explicit_owner(monkeypatch):
    tracker = _Tracker(_failed_task())
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)

    with pytest.raises(PermissionDeniedError, match="Only ROOT may specify a task owner"):
        await task_router.retry_task(
            "failed-task",
            task_router.RetryTaskRequest(
                owner_account_id="tenant-b",
                owner_user_id="bob",
            ),
            _ctx(),
        )

    assert tracker.get_calls == []


@pytest.mark.asyncio
async def test_retry_does_not_replay_a_failed_predecessor_after_operation_success(monkeypatch):
    tracker = _Tracker(_failed_task())
    tracker.resolved_task = TaskRecord(
        task_id="completed-retry",
        task_type="session_commit",
        status=TaskStatus.COMPLETED,
        resource_id="session-1",
        account_id="acme",
        user_id="alice",
        operation_id="failed-task",
        attempt_number=2,
    )
    service = _Service()
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)

    response = await task_router.retry_task("failed-task", task_router.RetryTaskRequest(), _ctx())

    assert response.result["disposition"] == "operation_resolved"
    assert response.result["task_id"] == "completed-retry"
    assert tracker.task.status == TaskStatus.COMPLETED
    assert service.sessions.calls == 0
    assert tracker.link_calls == []


@pytest.mark.asyncio
async def test_retry_reports_an_already_completed_task_without_starting_work(monkeypatch):
    task = _failed_task()
    task.status = TaskStatus.COMPLETED
    tracker = _Tracker(task)
    service = _Service()
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)

    response = await task_router.retry_task("failed-task", task_router.RetryTaskRequest(), _ctx())

    assert response.result["disposition"] == "operation_resolved"
    assert response.result["task_id"] == "failed-task"
    assert service.sessions.calls == 0


@pytest.mark.asyncio
async def test_retry_stops_after_the_linked_attempt_limit(monkeypatch):
    tracker = _Tracker(_failed_task(attempt_number=task_router.MAX_LINKED_RETRY_ATTEMPTS))
    service = _Service()
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_service", lambda: service)

    response = await task_router.retry_task("failed-task", task_router.RetryTaskRequest(), _ctx())

    assert response.result["disposition"] == "retry_limit_reached"
    assert service.sessions.calls == 0
