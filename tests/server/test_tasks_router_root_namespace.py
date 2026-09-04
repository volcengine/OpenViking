# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for ROOT task endpoints and account-scoped persistence (#4393).

In dev/trusted auth modes the ROOT caller still has a concrete
``account_id``/``user_id`` namespace whose task records are persisted under
``/local/<account>/_system/tasks/<user>/``. The ROOT branches of the task
routes must consult that namespace (via the tracker's owner-scoped store
loads), otherwise task history disappears from ``GET /api/v1/tasks`` after a
server restart and ``GET /api/v1/tasks/{id}``` returns 404 for older tasks.
"""

from typing import Any, Dict, List, Optional

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import tasks as tasks_router
from openviking.service.task_store import SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID
from openviking.service.task_tracker import TaskRecord, TaskStatus
from openviking_cli.session.user_id import UserIdentifier


class RecordingTracker:
    """Minimal tracker stand-in that records owner-scoped calls."""

    def __init__(
        self,
        *,
        store_records: Optional[Dict[tuple[str, str], List[TaskRecord]]] = None,
        cache_records: Optional[List[TaskRecord]] = None,
    ) -> None:
        self._store = store_records or {}
        self._cache = cache_records or []
        self.get_calls: List[Dict[str, Optional[str]]] = []
        self.list_calls: List[Dict[str, Any]] = []

    async def get(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        self.get_calls.append(
            {"task_id": task_id, "account_id": account_id, "user_id": user_id}
        )
        if account_id is None:
            for record in self._cache:
                if record.task_id == task_id:
                    return record
            return None
        for record in self._store.get((account_id, user_id or ""), []):
            if record.task_id == task_id:
                return record
        return None

    async def list_tasks(
        self,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        include_internal: bool = True,
    ) -> List[TaskRecord]:
        self.list_calls.append(
            {
                "task_type": task_type,
                "status": status,
                "resource_id": resource_id,
                "limit": limit,
                "account_id": account_id,
                "user_id": user_id,
                "include_internal": include_internal,
            }
        )
        if account_id is None:
            records = list(self._cache)
        else:
            records = list(self._store.get((account_id, user_id or ""), []))
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records[:limit]


def _record(task_id: str, *, created_at: float, task_type: str = "session_commit") -> TaskRecord:
    record = TaskRecord(
        task_id=task_id,
        task_type=task_type,
        account_id="default",
        user_id="default",
    )
    record.created_at = created_at
    record.updated_at = created_at
    record.status = TaskStatus.COMPLETED
    return record


@pytest.fixture
def dev_root_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


def _owner_list_calls(tracker: RecordingTracker) -> List[Dict[str, Any]]:
    return [call for call in tracker.list_calls if call["account_id"] is not None]


@pytest.mark.asyncio
async def test_list_tasks_root_loads_caller_namespace(monkeypatch, dev_root_ctx):
    """ROOT listing must query the caller's own namespace, not only the cache."""
    persisted = [
        _record("old-1", created_at=100.0),
        _record("old-2", created_at=50.0),
    ]
    tracker = RecordingTracker(
        store_records={("default", "default"): persisted},
        cache_records=[_record("fresh-1", created_at=200.0)],
    )
    monkeypatch.setattr(tasks_router, "get_task_tracker", lambda: tracker)

    response = await tasks_router.list_tasks(limit=50, _ctx=dev_root_ctx)

    assert response.status == "ok"
    task_ids = {task["task_id"] for task in response.result}
    assert task_ids == {"old-1", "old-2", "fresh-1"}
    owner_calls = _owner_list_calls(tracker)
    assert any(
        call["account_id"] == "default" and call["user_id"] == "default"
        for call in owner_calls
    ), "ROOT listing must load the caller namespace from the persistent store"
    assert any(
        call["account_id"] == SYSTEM_TASK_ACCOUNT_ID
        and call["user_id"] == SYSTEM_TASK_USER_ID
        for call in owner_calls
    ), "ROOT listing must keep merging the system namespace"


@pytest.mark.asyncio
async def test_list_tasks_root_results_sorted_most_recent_first(monkeypatch, dev_root_ctx):
    tracker = RecordingTracker(
        store_records={("default", "default"): [_record("old-1", created_at=100.0)]},
        cache_records=[_record("fresh-1", created_at=200.0)],
    )
    monkeypatch.setattr(tasks_router, "get_task_tracker", lambda: tracker)

    response = await tasks_router.list_tasks(limit=2, _ctx=dev_root_ctx)

    assert [task["task_id"] for task in response.result] == ["fresh-1", "old-1"]


@pytest.mark.asyncio
async def test_get_task_root_falls_back_to_caller_namespace(monkeypatch, dev_root_ctx):
    """After a restart the cache misses, so ROOT get must try the caller namespace."""
    persisted = _record("persisted-task", created_at=100.0)
    tracker = RecordingTracker(
        store_records={
            ("default", "default"): [persisted],
            (SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID): [
                _record("system-task", created_at=90.0)
            ],
        },
        cache_records=[],
    )
    monkeypatch.setattr(tasks_router, "get_task_tracker", lambda: tracker)

    response = await tasks_router.get_task("persisted-task", _ctx=dev_root_ctx)

    assert response.status == "ok"
    assert response.result["task_id"] == "persisted-task"
    assert {
        call["account_id"]: call["user_id"] for call in tracker.get_calls
    }.get("default") == "default"


@pytest.mark.asyncio
async def test_get_task_root_still_falls_back_to_system_namespace(monkeypatch, dev_root_ctx):
    persisted_system = _record("system-task", created_at=90.0)
    tracker = RecordingTracker(
        store_records={
            (SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID): [persisted_system],
        },
        cache_records=[],
    )
    monkeypatch.setattr(tasks_router, "get_task_tracker", lambda: tracker)

    response = await tasks_router.get_task("system-task", _ctx=dev_root_ctx)

    assert response.status == "ok"
    assert response.result["task_id"] == "system-task"


@pytest.mark.asyncio
async def test_get_task_root_cache_hit_skips_store(monkeypatch, dev_root_ctx):
    cached = _record("cached-task", created_at=300.0)
    tracker = RecordingTracker(cache_records=[cached])
    monkeypatch.setattr(tasks_router, "get_task_tracker", lambda: tracker)

    response = await tasks_router.get_task("cached-task", _ctx=dev_root_ctx)

    assert response.status == "ok"
    assert response.result["task_id"] == "cached-task"
    assert len(tracker.get_calls) == 1


@pytest.mark.asyncio
async def test_get_task_root_unknown_id_raises_not_found(monkeypatch, dev_root_ctx):
    tracker = RecordingTracker()
    monkeypatch.setattr(tasks_router, "get_task_tracker", lambda: tracker)

    with pytest.raises(Exception) as excinfo:
        await tasks_router.get_task("missing-task", _ctx=dev_root_ctx)
    assert "not found" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_list_tasks_user_role_queries_own_namespace(monkeypatch):
    user_ctx = RequestContext(
        user=UserIdentifier("default", "alice"), role=Role.USER
    )
    tracker = RecordingTracker(
        store_records={("default", "alice"): [_record("alice-task", created_at=10.0)]}
    )
    monkeypatch.setattr(tasks_router, "get_task_tracker", lambda: tracker)

    response = await tasks_router.list_tasks(limit=50, _ctx=user_ctx)

    assert response.status == "ok"
    assert [task["task_id"] for task in response.result] == ["alice-task"]
    assert tracker.list_calls[0]["account_id"] == "default"
    assert tracker.list_calls[0]["user_id"] == "alice"
