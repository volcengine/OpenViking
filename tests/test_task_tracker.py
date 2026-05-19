# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for TaskTracker."""

import json
import time

import pytest

from openviking.pyagfs.exceptions import AGFSAlreadyExistsError
from openviking.server.identity import RequestContext, Role
from openviking.service.session_service import SessionService
from openviking.service.task_store import InMemoryTaskStore, PersistentTaskStore
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
        "account_id": account_id,
        "user_id": user_id,
    }


def _make_ctx(account_id: str = "acme", user_id: str = "alice") -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id, user_id, "agent-1"),
        role=Role.ADMIN,
    )


class _FakeAgfs:
    def __init__(self):
        self.files = {}
        self.dirs = {"/", "/local"}

    def mkdir(self, path: str, mode: str = "755"):
        self.dirs.add(path.rstrip("/") or "/")
        return {"message": "created", "mode": mode}

    def write(self, path: str, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.files[path] = data
        parent = path.rsplit("/", 1)[0] or "/"
        self.dirs.add(parent)
        return "OK"

    def read(self, path: str, offset: int = 0, size: int = -1, stream: bool = False):
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        if size >= 0:
            return data[offset : offset + size]
        return data[offset:]

    def ls(self, path: str = "/"):
        prefix = path.rstrip("/") or "/"
        if prefix not in self.dirs:
            return []
        children = {}
        for directory in self.dirs:
            if directory in {prefix, "/"}:
                continue
            if directory.startswith(prefix + "/"):
                name = directory[len(prefix) + 1 :].split("/", 1)[0]
                if name:
                    children[name] = {"name": name, "path": f"{prefix}/{name}", "is_dir": True}
        for file_path in self.files:
            if file_path.startswith(prefix + "/"):
                name = file_path[len(prefix) + 1 :].split("/", 1)[0]
                if name and "/" not in file_path[len(prefix) + 1 :]:
                    children[name] = {"name": name, "path": f"{prefix}/{name}", "is_dir": False}
        return list(children.values())


class _FakeAgfsExistingDir(_FakeAgfs):
    def mkdir(self, path: str, mode: str = "755"):
        normalized = path.rstrip("/") or "/"
        if normalized in self.dirs:
            raise AGFSAlreadyExistsError(f"already exists: {path}")
        self.dirs.add(normalized)
        return {"message": "created", "mode": mode}


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
        account_id="acme",
        user_id="alice",
    )

    assert (
        tracker.get(
            task.task_id,
            account_id="acme",
            user_id="bob",
        )
        is None
    )


def test_list_tasks_filters_by_owner(tracker: TaskTracker):
    tracker.create(
        "session_commit",
        resource_id="alice-task",
        account_id="acme",
        user_id="alice",
    )
    tracker.create(
        "session_commit",
        resource_id="bob-task",
        account_id="acme",
        user_id="bob",
    )

    tasks = tracker.list_tasks(account_id="acme", user_id="alice")

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
        account_id="acme",
        user_id="alice",
    )
    bob_task = tracker.create_if_no_running(
        "reindex",
        "viking://resources/demo",
        account_id="acme",
        user_id="bob",
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
    assert isinstance(d["updated_at"], float)
    assert isinstance(d["created_at_iso"], str)
    assert "T" in d["created_at_iso"]
    assert isinstance(d["updated_at_iso"], str)
    assert "account_id" not in d
    assert "user_id" not in d


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


def test_persistent_store_cross_tracker_visibility():
    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker1 = TaskTracker(store=store)
    tracker2 = TaskTracker(store=store)

    task = tracker1.create("session_commit", resource_id="sess-123", **_owner_kwargs())
    tracker1.start(task.task_id, account_id="acme", user_id="alice")
    tracker1.complete(task.task_id, {"ok": True}, account_id="acme", user_id="alice")

    loaded = tracker2.get(task.task_id, account_id="acme", user_id="alice")

    assert loaded is not None
    assert loaded.status == TaskStatus.COMPLETED
    assert loaded.result == {"ok": True}


def test_persistent_store_writes_task_record_json():
    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store=store)

    task = tracker.create("add_resource", resource_id="viking://resources/demo", **_owner_kwargs())

    raw = agfs.files[f"/local/acme/tasks/alice/{task.task_id}.json"]
    payload = json.loads(raw.decode("utf-8"))

    assert payload["task_id"] == task.task_id
    assert payload["task_type"] == "add_resource"
    assert payload["account_id"] == "acme"
    assert payload["user_id"] == "alice"
    assert "schema_version" not in payload


def test_inmemory_store_keeps_tasktracker_tasks_dict():
    tracker = TaskTracker(store=InMemoryTaskStore())
    task = tracker.create("session_commit", **_owner_kwargs())
    assert task.task_id in tracker._tasks


def test_persistent_store_survives_tracker_reset():
    agfs = _FakeAgfs()
    tracker1 = TaskTracker(store=PersistentTaskStore(agfs))
    task = tracker1.create("session_commit", resource_id="sess-123", **_owner_kwargs())
    tracker1.start(task.task_id, account_id="acme", user_id="alice")

    tracker2 = TaskTracker(store=PersistentTaskStore(agfs))
    loaded = tracker2.get(task.task_id, account_id="acme", user_id="alice")

    assert loaded is not None
    assert loaded.status == TaskStatus.RUNNING


def test_persistent_store_ignores_existing_task_dirs():
    agfs = _FakeAgfsExistingDir()
    tracker = TaskTracker(store=PersistentTaskStore(agfs))

    first = tracker.create("session_commit", resource_id="sess-1", **_owner_kwargs())
    second = tracker.create("session_commit", resource_id="sess-2", **_owner_kwargs())

    assert first.task_id != second.task_id
    assert agfs.files[f"/local/acme/tasks/alice/{first.task_id}.json"]
    assert agfs.files[f"/local/acme/tasks/alice/{second.task_id}.json"]


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
            account_id="",
            user_id="alice",
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


@pytest.mark.asyncio
async def test_session_service_get_commit_task_also_filters_account():
    tracker = get_task_tracker()
    task = tracker.create("session_commit", resource_id="sess-123", **_owner_kwargs())
    service = SessionService()

    other_account_result = await service.get_commit_task(
        task.task_id,
        _make_ctx(account_id="other-acme", user_id="alice"),
    )

    assert other_account_result is None
