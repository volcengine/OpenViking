# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for TaskTracker."""

import time

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.session_service import SessionService
from openviking.service.task_tracker import (
    TaskStatus,
    TaskTracker,
    _sanitize_error,
    get_task_tracker,
    reset_task_tracker,
)
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def clean_singleton():
    """Reset singleton before and after each test."""
    reset_task_tracker()
    yield
    reset_task_tracker()


@pytest.fixture
def tracker() -> TaskTracker:
    return TaskTracker()


def _owner_kwargs(account_id: str = "acme", user_id: str = "alice"):
    return {
        "owner_account_id": account_id,
        "owner_user_id": user_id,
    }


def _make_ctx(account_id: str = "acme", user_id: str = "alice") -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id, user_id, "agent-1"),
        role=Role.ADMIN,
    )


# ── Basic CRUD ──


def test_create_task(tracker: TaskTracker):
    task = tracker.create("session_commit", resource_id="sess-123", **_owner_kwargs())
    assert task.task_id
    assert task.task_type == "session_commit"
    assert task.resource_id == "sess-123"
    assert task.status == TaskStatus.PENDING


def test_start_task(tracker: TaskTracker):
    task = tracker.create("session_commit", **_owner_kwargs())
    tracker.start(task.task_id)
    retrieved = tracker.get(task.task_id)
    assert retrieved is not None
    assert retrieved.status == TaskStatus.RUNNING


def test_complete_task(tracker: TaskTracker):
    task = tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    tracker.start(task.task_id)
    tracker.complete(task.task_id, {"memories_extracted": 3})
    retrieved = tracker.get(task.task_id)
    assert retrieved is not None
    assert retrieved.status == TaskStatus.COMPLETED
    assert retrieved.result == {"memories_extracted": 3}


def test_fail_task(tracker: TaskTracker):
    task = tracker.create("session_commit", **_owner_kwargs())
    tracker.start(task.task_id)
    tracker.fail(task.task_id, "LLM timeout")
    retrieved = tracker.get(task.task_id)
    assert retrieved is not None
    assert retrieved.status == TaskStatus.FAILED
    assert "LLM timeout" in retrieved.error


def test_get_nonexistent_returns_none(tracker: TaskTracker):
    assert tracker.get("does-not-exist") is None


# ── List / Filter ──


def test_list_all(tracker: TaskTracker):
    tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    tracker.create("resource_ingest", resource_id="r1", **_owner_kwargs())
    tasks = tracker.list_tasks()
    assert len(tasks) == 2


def test_list_filter_by_type(tracker: TaskTracker):
    tracker.create("session_commit", **_owner_kwargs())
    tracker.create("resource_ingest", **_owner_kwargs())
    tasks = tracker.list_tasks(task_type="session_commit")
    assert len(tasks) == 1
    assert tasks[0].task_type == "session_commit"


def test_list_filter_by_status(tracker: TaskTracker):
    t1 = tracker.create("session_commit", **_owner_kwargs())
    tracker.create("session_commit", **_owner_kwargs())
    tracker.start(t1.task_id)
    tracker.complete(t1.task_id, {})

    completed = tracker.list_tasks(status="completed")
    assert len(completed) == 1
    pending = tracker.list_tasks(status="pending")
    assert len(pending) == 1


def test_list_filter_by_resource_id(tracker: TaskTracker):
    tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    tracker.create("session_commit", resource_id="s2", **_owner_kwargs())
    tasks = tracker.list_tasks(resource_id="s1")
    assert len(tasks) == 1
    assert tasks[0].resource_id == "s1"


def test_get_hides_task_from_other_owner(tracker: TaskTracker):
    task = tracker.create(
        "session_commit",
        resource_id="s1",
        owner_account_id="acme",
        owner_user_id="alice",
    )

    assert (
        tracker.get(
            task.task_id,
            owner_account_id="acme",
            owner_user_id="bob",
        )
        is None
    )


def test_list_tasks_filters_by_owner(tracker: TaskTracker):
    tracker.create(
        "session_commit",
        resource_id="alice-task",
        owner_account_id="acme",
        owner_user_id="alice",
    )
    tracker.create(
        "session_commit",
        resource_id="bob-task",
        owner_account_id="acme",
        owner_user_id="bob",
    )

    tasks = tracker.list_tasks(owner_account_id="acme", owner_user_id="alice")

    assert len(tasks) == 1
    assert tasks[0].resource_id == "alice-task"


def test_list_limit(tracker: TaskTracker):
    for i in range(10):
        tracker.create("session_commit", resource_id=f"s{i}", **_owner_kwargs())
    tasks = tracker.list_tasks(limit=3)
    assert len(tasks) == 3


def test_list_order_most_recent_first(tracker: TaskTracker):
    tracker.create("session_commit", resource_id="first", **_owner_kwargs())
    tracker.create("session_commit", resource_id="second", **_owner_kwargs())
    tasks = tracker.list_tasks()
    assert tasks[0].resource_id == "second"
    assert tasks[1].resource_id == "first"


# ── Duplicate detection ──


def test_has_running_detects_pending(tracker: TaskTracker):
    tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    assert tracker.has_running("session_commit", "s1") is True


def test_has_running_detects_running(tracker: TaskTracker):
    t = tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    tracker.start(t.task_id)
    assert tracker.has_running("session_commit", "s1") is True


def test_has_running_false_after_complete(tracker: TaskTracker):
    t = tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    tracker.start(t.task_id)
    tracker.complete(t.task_id, {})
    assert tracker.has_running("session_commit", "s1") is False


def test_has_running_false_after_fail(tracker: TaskTracker):
    t = tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    tracker.start(t.task_id)
    tracker.fail(t.task_id, "error")
    assert tracker.has_running("session_commit", "s1") is False


def test_create_if_no_running_isolated_by_owner(tracker: TaskTracker):
    alice_task = tracker.create_if_no_running(
        "reindex",
        "viking://resources/demo",
        owner_account_id="acme",
        owner_user_id="alice",
    )
    bob_task = tracker.create_if_no_running(
        "reindex",
        "viking://resources/demo",
        owner_account_id="acme",
        owner_user_id="bob",
    )

    assert alice_task is not None
    assert bob_task is not None
    assert alice_task.task_id != bob_task.task_id


# ── Serialization ──


def test_to_dict(tracker: TaskTracker):
    task = tracker.create(
        "session_commit",
        resource_id="s1",
        **_owner_kwargs(),
    )
    d = task.to_dict()
    assert d["task_id"] == task.task_id
    assert d["status"] == "pending"
    assert d["task_type"] == "session_commit"
    assert d["resource_id"] == "s1"
    assert isinstance(d["created_at"], float)
    assert "owner_account_id" not in d
    assert "owner_user_id" not in d


# ── Sanitization ──


def test_sanitize_removes_sk_key():
    assert "[REDACTED]" in _sanitize_error("Error with sk-ant-api03-DAqSxxxxx")


def test_sanitize_removes_ghp_token():
    assert "[REDACTED]" in _sanitize_error("Auth failed ghp_" + "x" * 36)


def test_sanitize_removes_bearer_token():
    assert "[REDACTED]" in _sanitize_error("Bearer xoxb-1234567890-abcdefghij")


def test_sanitize_truncates_long_error():
    long_error = "x" * 1000
    sanitized = _sanitize_error(long_error)
    assert len(sanitized) <= 520  # 500 + "...[truncated]"
    assert sanitized.endswith("...[truncated]")


def test_sanitize_preserves_safe_error():
    safe = "LLM timeout after 30s"
    assert _sanitize_error(safe) == safe


# ── TTL / Eviction ──


def test_evict_expired_completed(tracker: TaskTracker):
    t = tracker.create("session_commit", **_owner_kwargs())
    tracker.start(t.task_id)
    tracker.complete(t.task_id, {})
    # Simulate old timestamp (access internal state; get() returns defensive copies)
    tracker._tasks[t.task_id].updated_at = time.time() - tracker.TTL_COMPLETED - 1
    tracker._evict_expired()
    assert tracker.get(t.task_id) is None


def test_evict_keeps_recent_completed(tracker: TaskTracker):
    t = tracker.create("session_commit", **_owner_kwargs())
    tracker.start(t.task_id)
    tracker.complete(t.task_id, {})
    tracker._evict_expired()
    assert tracker.get(t.task_id) is not None


def test_evict_fifo_when_over_limit(tracker: TaskTracker):
    tracker.MAX_TASKS = 5
    tasks = []
    for i in range(7):
        tasks.append(tracker.create("session_commit", resource_id=f"s{i}", **_owner_kwargs()))
    tracker._evict_expired()
    assert tracker.count() == 5
    # Oldest should be gone
    assert tracker.get(tasks[0].task_id) is None
    assert tracker.get(tasks[1].task_id) is None
    # Newest should remain
    assert tracker.get(tasks[6].task_id) is not None


# ── Singleton ──


def test_singleton():
    t1 = get_task_tracker()
    t2 = get_task_tracker()
    assert t1 is t2


def test_singleton_reset():
    t1 = get_task_tracker()
    reset_task_tracker()
    t2 = get_task_tracker()
    assert t1 is not t2


def test_create_requires_owner(tracker: TaskTracker):
    with pytest.raises(TypeError):
        tracker.create("session_commit", resource_id="sess-123")


def test_create_if_no_running_requires_owner(tracker: TaskTracker):
    with pytest.raises(TypeError):
        tracker.create_if_no_running("reindex", "viking://resources/demo")


def test_create_rejects_blank_owner_values(tracker: TaskTracker):
    with pytest.raises(ValueError, match="Task ownership requires"):
        tracker.create(
            "session_commit",
            resource_id="sess-123",
            owner_account_id="",
            owner_user_id="alice",
        )


@pytest.mark.asyncio
async def test_session_service_get_commit_task_is_owner_scoped():
    tracker = get_task_tracker()
    task = tracker.create("session_commit", resource_id="sess-123", **_owner_kwargs())
    service = SessionService()

    owner_result = await service.get_commit_task(task.task_id, _make_ctx())
    other_result = await service.get_commit_task(task.task_id, _make_ctx(user_id="bob"))

    assert owner_result is not None
    assert owner_result["task_id"] == task.task_id
    assert owner_result["resource_id"] == "sess-123"
    assert other_result is None
