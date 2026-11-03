# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for TaskTracker."""

import asyncio
import json
import threading
import time
from copy import deepcopy
from unittest.mock import create_autospec

import pytest

from openviking.pyagfs.exceptions import AGFSAlreadyExistsError, AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.service.session_service import SessionService
from openviking.service.task_domain import TaskPatch
from openviking.service.task_store import (
    SYSTEM_TASK_ACCOUNT_ID,
    PersistentTaskStore,
    _task_from_payload,
    _task_to_payload,
)
from openviking.service.task_tracker import (
    TaskStatus,
    TaskTracker,
    _sanitize_error,
    get_task_tracker,
    run_tracked_operation,
    set_task_tracker,
    task_admission,
)
from openviking.service.task_work_index import get_task_context
from openviking_cli.exceptions import DeadlineExceededError, FailedPreconditionError
from openviking_cli.session.user_id import UserIdentifier

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def clean_singleton():
    """Reset singleton before and after each test."""
    set_task_tracker(None)
    yield
    set_task_tracker(None)


@pytest.fixture
def tracker() -> TaskTracker:
    return TaskTracker(store=PersistentTaskStore(_FakeAgfs()))


def _owner_kwargs(account_id: str = "acme", user_id: str = "alice"):
    return {
        "account_id": account_id,
        "user_id": user_id,
    }


def _make_ctx(account_id: str = "acme", user_id: str = "alice") -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id, user_id),
        role=Role.ADMIN,
    )


def _set_fake_global_tracker() -> TaskTracker:
    tracker = TaskTracker(store=PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    return tracker


class _FakeAgfs:
    def __init__(self):
        self.files = {}
        self.dirs = {"/", "/local"}
        self.fail_rm = False
        self.mkdir_calls = []
        self.write_calls = []
        self.rm_calls = []

    def mkdir(self, path: str, mode: str = "755"):
        self.mkdir_calls.append({"path": path, "mode": mode})
        self.dirs.add(path.rstrip("/") or "/")
        return {"message": "created", "mode": mode}

    def write(
        self,
        path: str,
        data,
        max_retries: int = 3,
        *,
        ctx=None,
    ):
        self.write_calls.append(
            {
                "path": path,
                "max_retries": max_retries,
                "ctx": ctx,
            }
        )
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

    def rm(
        self,
        path: str,
        recursive: bool = False,
        force: bool = False,
        *,
        ctx=None,
    ):
        self.rm_calls.append(
            {
                "path": path,
                "recursive": recursive,
                "force": force,
                "ctx": ctx,
            }
        )
        if self.fail_rm:
            raise OSError("simulated delete failure")
        if path not in self.files:
            raise AGFSNotFoundError(path)
        del self.files[path]
        return {"message": "removed", "recursive": recursive, "force": force}


class _FakeAgfsExistingDir(_FakeAgfs):
    def mkdir(self, path: str, mode: str = "755"):
        self.mkdir_calls.append({"path": path, "mode": mode})
        normalized = path.rstrip("/") or "/"
        if normalized in self.dirs:
            raise AGFSAlreadyExistsError(f"already exists: {path}")
        self.dirs.add(normalized)
        return {"message": "created", "mode": mode}


# ── Basic CRUD ──


async def test_create_task(tracker: TaskTracker):
    task = await tracker.create("session_commit", resource_id="sess-123", **_owner_kwargs())
    assert task.task_id
    assert task.task_type == "session_commit"
    assert task.resource_id == "sess-123"
    assert task.status == TaskStatus.PENDING


async def test_deletion_guard_refuses_new_tasks_for_deleting_identities(tracker: TaskTracker):
    existing = await tracker.create("add_resource", task_id="replayed", **_owner_kwargs())
    deleting = {("acme", "alice")}

    async def guard(account_id, user_id):
        return (account_id, user_id) in deleting

    tracker.set_deletion_guard(guard)

    with pytest.raises(FailedPreconditionError):
        await tracker.create("add_resource", task_id="late", **_owner_kwargs())
    assert await tracker.get("late", **_owner_kwargs()) is None
    replayed = await tracker.create("add_resource", task_id="replayed", **_owner_kwargs())
    assert replayed.task_id == existing.task_id
    assert await tracker.create("add_resource", **_owner_kwargs(user_id="bob"))
    assert await tracker.create("account_delete", account_id=SYSTEM_TASK_ACCOUNT_ID, user_id="root")


async def test_identity_cleanup_task_creation_allows_deleting_owner(tracker: TaskTracker):
    async def guard(account_id, user_id):
        return (account_id, user_id) == ("acc", "alice")

    tracker.set_deletion_guard(guard)

    task = await tracker.create(
        "user_delete",
        task_id="cleanup",
        account_id="acc",
        user_id="alice",
    )
    assert task.task_id == "cleanup"
    with pytest.raises(FailedPreconditionError):
        await tracker.create("add_resource", account_id="acc", user_id="alice")


async def test_wait_uses_short_initial_poll(monkeypatch, tracker: TaskTracker):
    task = await tracker.create("add_resource", task_id="task", **_owner_kwargs())
    delays = []

    async def sleep(delay):
        delays.append(delay)
        await tracker.complete(task.task_id, {}, **_owner_kwargs())

    monkeypatch.setattr(asyncio, "sleep", sleep)

    assert (await tracker.wait(task.task_id, **_owner_kwargs())).status is TaskStatus.COMPLETED
    assert delays == [0.05]


async def test_wait_for_task_work_uses_short_initial_poll(monkeypatch, tracker: TaskTracker):
    task = await tracker.create("add_resource", task_id="task", **_owner_kwargs())
    await tracker.start(task.task_id, **_owner_kwargs())
    await tracker.register_work(task.task_id, "work", "Semantic", **_owner_kwargs())
    delays = []

    async def sleep(delay):
        delays.append(delay)
        await tracker.mark_work_done(task.task_id, "work", **_owner_kwargs())

    monkeypatch.setattr(asyncio, "sleep", sleep)

    await tracker.wait_for_task_work(task.task_id, **_owner_kwargs())
    assert delays == [0.05]


@pytest.mark.parametrize("failure", ["deletion", "identity-query", "watch-admission"])
async def test_creation_rejected_after_insert_withdraws_the_task(tracker, failure):
    deleting = set()
    store_create = tracker._store.create
    error = ConnectionError("Admission storage unavailable")

    async def guard(account_id, user_id):
        if (account_id, user_id) not in deleting:
            return False
        if failure == "identity-query":
            raise error
        return failure == "deletion"

    async def admitted():
        if deleting and failure == "watch-admission":
            raise error
        return True

    async def create_after_cleanup(task, **kwargs):
        # The check has passed; deletion fences the identity and finishes
        # listing Tasks before this insert lands.
        deleting.add((task.account_id, task.user_id))
        assert await tracker.list_tasks(**_owner_kwargs(), limit=None) == []
        return await store_create(task, **kwargs)

    tracker.set_deletion_guard(guard)
    tracker._store.create = create_after_cleanup

    with task_admission("late", admitted):
        with pytest.raises(
            FailedPreconditionError if failure == "deletion" else ConnectionError
        ) as raised:
            await tracker.create(
                "connector_import", resource_id="resource", task_id="late", **_owner_kwargs()
            )
    if failure != "deletion":
        assert raised.value is error
    assert await tracker.get("late", **_owner_kwargs()) is None
    tracker._store.create = store_create
    deleting.clear()
    assert await tracker.create_if_no_running("connector_import", "resource", **_owner_kwargs())


@pytest.mark.parametrize("cancel_at", ["insert", "identity-query", "watch-admission"])
async def test_cancel_during_task_creation_withdraws_the_unaccepted_task(cancel_at):
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    class BlockingAgfs(_FakeAgfs):
        def write(self, path, data, *args, **kwargs):
            if cancel_at == "insert" and json.loads(data)["status"] == "pending":
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(3)
            return super().write(path, data, *args, **kwargs)

    agfs = BlockingAgfs()
    tracker = TaskTracker(PersistentTaskStore(agfs))
    checks = 0

    async def check():
        nonlocal checks
        checks += 1
        if checks == 2:
            entered.set()
            await asyncio.Event().wait()

    async def guard(account_id, user_id):
        if cancel_at == "identity-query":
            await check()
        return False

    async def admitted():
        if cancel_at == "watch-admission":
            await check()
        return True

    tracker.set_deletion_guard(guard)
    with task_admission("late", admitted):
        running = asyncio.create_task(
            tracker.create("connector_import", task_id="late", **_owner_kwargs())
        )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        running.cancel()
        await asyncio.sleep(0)
        running.cancel()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 1)

    assert await TaskTracker(PersistentTaskStore(agfs)).list_tasks(**_owner_kwargs()) == []


async def test_admission_gates_only_its_own_task_id(tracker: TaskTracker):
    async def refused():
        return False

    with task_admission("run-1", refused):
        with pytest.raises(FailedPreconditionError):
            await tracker.create("add_resource", task_id="run-1", **_owner_kwargs())
        assert await tracker.create("add_resource", task_id="other", **_owner_kwargs())
    assert await tracker.get("run-1", **_owner_kwargs()) is None
    assert await tracker.create("add_resource", task_id="run-1", **_owner_kwargs())


async def test_start_task(tracker: TaskTracker):
    task = await tracker.create("session_commit", **_owner_kwargs())
    await tracker.start(task.task_id)
    retrieved = await tracker.get(task.task_id)
    assert retrieved is not None
    assert retrieved.status == TaskStatus.RUNNING


async def test_patch_stage_and_record_owner_cancelled(tracker: TaskTracker):
    task = await tracker.create("add_resource", **_owner_kwargs())
    await tracker.start(task.task_id, stage="queued")
    await tracker.patch_task(task.task_id, TaskPatch(stage="parsing"), **_owner_kwargs())
    retrieved = await tracker.get(task.task_id)
    assert retrieved is not None
    assert retrieved.status == TaskStatus.RUNNING
    assert retrieved.stage == "parsing"

    await tracker.record_cancelled(task.task_id)
    cancelled = await tracker.get(task.task_id)
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.stage == "cancelled"


async def test_patch_task_updates_fields_and_dispatch_marker_atomically(tracker: TaskTracker):
    task = await tracker.create("content_write", **_owner_kwargs())
    await tracker.start(task.task_id)

    patch = await tracker.patch_task(
        task.task_id,
        TaskPatch(
            result={"semantic_status": "deferred", "vector_status": "queued"},
            stage="dispatched",
            meta={"request": {"attempt": 1}},
            auth={"source": "content_write"},
            add_enqueue_complete_work_ids=frozenset({"root-work-1"}),
        ),
        **_owner_kwargs(),
    )

    assert patch.is_applied
    stored = await tracker.get(task.task_id, **_owner_kwargs())
    assert stored is not None
    assert stored.status == TaskStatus.RUNNING
    assert stored.result == {"semantic_status": "deferred", "vector_status": "queued"}
    assert stored.stage == "dispatched"
    assert stored.meta == {"request": {"attempt": 1}}
    assert await tracker.get_task_auth(task.task_id, **_owner_kwargs()) == {
        "source": "content_write",
        "enqueue_complete_work_ids": ["root-work-1"],
    }

    cleared = await tracker.patch_task(
        task.task_id,
        TaskPatch(clear_fields=frozenset({"result", "stage"})),
        **_owner_kwargs(),
    )

    assert cleared.is_applied
    stored = await tracker.get(task.task_id, **_owner_kwargs())
    assert stored is not None
    assert stored.result is None
    assert stored.stage is None


async def test_complete_task(tracker: TaskTracker):
    task = await tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    await tracker.start(task.task_id)
    await tracker.complete(task.task_id, {"memories_extracted": 3})
    retrieved = await tracker.get(task.task_id)
    assert retrieved is not None
    assert retrieved.status == TaskStatus.COMPLETED
    assert retrieved.stage == "completed"
    assert retrieved.result == {"memories_extracted": 3}


async def test_complete_task_updates_resource_id(tracker: TaskTracker):
    task = await tracker.create("add_resource", resource_id=None, **_owner_kwargs())

    await tracker.complete(
        task.task_id,
        {"root_uri": "viking://resources/real-title"},
        resource_id="viking://resources/real-title",
    )

    retrieved = await tracker.get(task.task_id)
    assert retrieved is not None
    assert retrieved.resource_id == "viking://resources/real-title"
    assert await tracker.list_tasks(resource_id="viking://resources/real-title") == [retrieved]


async def test_task_result_redacts_user_key(tracker: TaskTracker):
    task = await tracker.create("admin_reindex", **_owner_kwargs())
    await tracker.complete(
        task.task_id,
        {
            "created_users": [{"account_id": "acme", "user_id": "bob", "user_key": "secret-key"}],
            "nested": {"user_key": "nested-secret"},
        },
    )

    retrieved = await tracker.get(task.task_id)

    assert retrieved is not None
    assert retrieved.result == {
        "created_users": [{"account_id": "acme", "user_id": "bob"}],
        "nested": {},
    }
    assert "user_key" not in json.dumps(retrieved.to_dict())


async def test_fail_task(tracker: TaskTracker):
    task = await tracker.create("session_commit", **_owner_kwargs())
    await tracker.start(task.task_id)
    await tracker.fail(task.task_id, "LLM timeout")
    retrieved = await tracker.get(task.task_id)
    assert retrieved is not None
    assert retrieved.status == TaskStatus.FAILED
    assert retrieved.stage == "failed"
    assert "LLM timeout" in retrieved.error


async def test_mark_externally_cancelled_task_without_enabling_active_cancel(
    tracker: TaskTracker,
):
    task = await tracker.create("connector_import", **_owner_kwargs())
    await tracker.start(task.task_id)

    with pytest.raises(ValueError, match="does not support cancellation"):
        await tracker.cancel(task.task_id, **_owner_kwargs())

    await tracker.mark_cancelled(task.task_id, **_owner_kwargs())

    retrieved = await tracker.get(task.task_id)
    assert retrieved is not None
    assert retrieved.status == TaskStatus.CANCELLED
    assert retrieved.stage == "cancelled"


async def test_get_nonexistent_returns_none(tracker: TaskTracker):
    assert await tracker.get("does-not-exist") is None


# ── List / Filter ──


async def test_list_all(tracker: TaskTracker):
    await tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    await tracker.create("resource_ingest", resource_id="r1", **_owner_kwargs())
    tasks = await tracker.list_tasks()
    assert len(tasks) == 2


async def test_list_filter_by_type(tracker: TaskTracker):
    await tracker.create("session_commit", **_owner_kwargs())
    await tracker.create("resource_ingest", **_owner_kwargs())
    tasks = await tracker.list_tasks(task_type="session_commit")
    assert len(tasks) == 1
    assert tasks[0].task_type == "session_commit"


async def test_list_filter_by_status(tracker: TaskTracker):
    t1 = await tracker.create("session_commit", **_owner_kwargs())
    await tracker.create("session_commit", **_owner_kwargs())
    await tracker.start(t1.task_id)
    await tracker.complete(t1.task_id, {})

    completed = await tracker.list_tasks(status="completed")
    assert len(completed) == 1
    pending = await tracker.list_tasks(status="pending")
    assert len(pending) == 1


async def test_list_filter_by_resource_id(tracker: TaskTracker):
    await tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    await tracker.create("session_commit", resource_id="s2", **_owner_kwargs())
    tasks = await tracker.list_tasks(resource_id="s1")
    assert len(tasks) == 1
    assert tasks[0].resource_id == "s1"


async def test_get_hides_task_from_other_owner(tracker: TaskTracker):
    task = await tracker.create(
        "session_commit",
        resource_id="s1",
        account_id="acme",
        user_id="alice",
    )

    assert (
        await tracker.get(
            task.task_id,
            account_id="acme",
            user_id="bob",
        )
        is None
    )


async def test_list_tasks_filters_by_owner(tracker: TaskTracker):
    await tracker.create(
        "session_commit",
        resource_id="alice-task",
        account_id="acme",
        user_id="alice",
    )
    await tracker.create(
        "session_commit",
        resource_id="bob-task",
        account_id="acme",
        user_id="bob",
    )

    tasks = await tracker.list_tasks(account_id="acme", user_id="alice")

    assert len(tasks) == 1
    assert tasks[0].resource_id == "alice-task"


async def test_list_limit(tracker: TaskTracker):
    for i in range(10):
        await tracker.create(
            "session_commit",
            resource_id=f"s{i}",
            meta={"nested": {"values": [i]}},
            **_owner_kwargs(),
        )
    tasks = await tracker.list_tasks(limit=3)
    assert [task.resource_id for task in tasks] == ["s9", "s8", "s7"]
    tasks[0].meta["nested"]["values"].append("changed")
    await tracker.start(tasks[0].task_id)
    current = await tracker.list_tasks(limit=1)
    assert current[0].meta["nested"]["values"] == [9]
    assert current[0].status == TaskStatus.RUNNING
    assert tasks[0].status == TaskStatus.PENDING


async def test_list_can_hide_internal_tasks_before_limit(tracker: TaskTracker):
    visible = await tracker.create("add_resource", meta={}, **_owner_kwargs())
    internal = await tracker.create("add_resource", meta={"internal": True}, **_owner_kwargs())

    assert [task.task_id for task in await tracker.list_tasks(limit=1)] == [internal.task_id]
    assert [task.task_id for task in await tracker.list_tasks(limit=1, include_internal=False)] == [
        visible.task_id
    ]


async def test_list_order_most_recent_first(tracker: TaskTracker):
    await tracker.create("session_commit", resource_id="first", **_owner_kwargs())
    await tracker.create("session_commit", resource_id="second", **_owner_kwargs())
    tasks = await tracker.list_tasks()
    assert tasks[0].resource_id == "second"
    assert tasks[1].resource_id == "first"


# ── Duplicate detection ──


async def test_has_running_detects_pending(tracker: TaskTracker):
    await tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    assert await tracker.has_running("session_commit", "s1") is True


async def test_has_running_detects_running(tracker: TaskTracker):
    t = await tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    await tracker.start(t.task_id)
    assert await tracker.has_running("session_commit", "s1") is True


async def test_has_running_false_after_complete(tracker: TaskTracker):
    t = await tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    await tracker.start(t.task_id)
    await tracker.complete(t.task_id, {})
    assert await tracker.has_running("session_commit", "s1") is False


async def test_has_running_false_after_fail(tracker: TaskTracker):
    t = await tracker.create("session_commit", resource_id="s1", **_owner_kwargs())
    await tracker.start(t.task_id)
    await tracker.fail(t.task_id, "error")
    assert await tracker.has_running("session_commit", "s1") is False


async def test_create_if_no_running_isolated_by_owner(tracker: TaskTracker):
    alice_task = await tracker.create_if_no_running(
        "reindex",
        "viking://resources/demo",
        account_id="acme",
        user_id="alice",
    )
    bob_task = await tracker.create_if_no_running(
        "reindex",
        "viking://resources/demo",
        account_id="acme",
        user_id="bob",
    )

    assert alice_task is not None
    assert bob_task is not None
    assert alice_task.task_id != bob_task.task_id

    replay = await tracker.create_if_no_running(
        "reindex",
        "viking://resources/demo",
        account_id="acme",
        user_id="alice",
        task_id=alice_task.task_id,
    )
    assert replay.task_id == alice_task.task_id
    with pytest.raises(ValueError, match="Task ID belongs to another operation"):
        await tracker.create_if_no_running(
            "reindex",
            "viking://resources/other",
            account_id="acme",
            user_id="alice",
            task_id=alice_task.task_id,
        )


# ── Serialization ──


async def test_to_dict(tracker: TaskTracker):
    task = await tracker.create(
        "session_commit",
        resource_id="s1",
        auth={"provider": "git_http_basic", "password": "secret"},
        **_owner_kwargs(),
    )
    d = task.to_dict()
    assert d["task_id"] == task.task_id
    assert d["status"] == "pending"
    assert d["task_type"] == "session_commit"
    assert d["resource_id"] == "s1"
    assert d["stage"] is None
    assert isinstance(d["created_at"], float)
    assert isinstance(d["updated_at"], float)
    assert isinstance(d["created_at_iso"], str)
    assert "T" in d["created_at_iso"]
    assert isinstance(d["updated_at_iso"], str)
    assert "account_id" not in d
    assert "user_id" not in d
    assert "auth" not in d
    assert await tracker.get_task_auth(task.task_id, **_owner_kwargs()) == {
        "provider": "git_http_basic",
        "password": "secret",
    }
    await tracker.patch_task(
        task.task_id,
        TaskPatch(auth={"external_task_id": "session-1"}),
        **_owner_kwargs(),
    )
    assert await tracker.get_task_auth(task.task_id, **_owner_kwargs()) == {
        "provider": "git_http_basic",
        "password": "secret",
        "external_task_id": "session-1",
    }
    assert (await tracker.get(task.task_id, **_owner_kwargs())).auth == {}
    assert (await tracker.list_tasks(**_owner_kwargs()))[0].auth == {}


async def test_public_serialization_skips_private_payloads(tracker: TaskTracker):
    class PrivatePayload(dict):
        def items(self):
            raise AssertionError("Private payload was traversed")

        def __deepcopy__(self, memo):
            raise AssertionError("Private payload was copied")

    task = await tracker.create("session_commit", **_owner_kwargs())
    task.auth = PrivatePayload()
    task._extra_fields = PrivatePayload()
    task.meta = {"nested": [{"user_key": "secret", "api_key": "compile-key", "values": [1]}]}
    task.result = {"nested": [{"user_key": "secret", "api_key": "compile-key", "values": [2]}]}

    public = task.to_dict()
    assert set(public) == {
        "processing_seconds",
        "task_id",
        "task_type",
        "status",
        "created_at",
        "updated_at",
        "resource_id",
        "meta",
        "stage",
        "result",
        "error",
        "created_at_iso",
        "updated_at_iso",
    }
    assert public["meta"] == {"nested": [{"values": [1]}]}
    assert public["result"] == {"nested": [{"values": [2]}]}
    public["meta"]["nested"][0]["values"].append(3)
    public["result"]["nested"][0]["values"].append(4)
    assert task.meta["nested"][0]["values"] == [1]
    assert task.result["nested"][0]["values"] == [2]


# ── Sanitization ──


async def test_sanitize_removes_sk_key():
    assert "[REDACTED]" in _sanitize_error("Error with sk-ant-api03-DAqSxxxxx")


async def test_sanitize_removes_ghp_token():
    assert "[REDACTED]" in _sanitize_error("Auth failed ghp_" + "x" * 36)


async def test_sanitize_removes_bearer_token():
    assert "[REDACTED]" in _sanitize_error("Bearer xoxb-1234567890-abcdefghij")


async def test_sanitize_truncates_long_error():
    long_error = "x" * 1000
    sanitized = _sanitize_error(long_error)
    assert len(sanitized) <= 520  # 500 + "...[truncated]"
    assert sanitized.endswith("...[truncated]")


async def test_sanitize_preserves_safe_error():
    safe = "LLM timeout after 30s"
    assert _sanitize_error(safe) == safe


# ── TTL / Eviction ──


@pytest.mark.parametrize("already_missing", [False, True])
async def test_evict_expired_completed(already_missing):
    agfs = _FakeAgfs()
    writer = TaskTracker(store=PersistentTaskStore(agfs))
    task = await writer.create("session_commit", **_owner_kwargs())
    await writer.start(task.task_id)
    await writer.complete(task.task_id, {})
    path = f"/local/acme/_system/tasks/alice/{task.task_id}.json"
    if already_missing:
        agfs.files.clear()
    else:
        payload = json.loads(agfs.files[path])
        payload["updated_at"] = time.time() - writer.TTL_COMPLETED - 1
        agfs.files[path] = json.dumps(payload).encode()

    tracker = TaskTracker(store=PersistentTaskStore(agfs))
    await tracker.delete_expired()
    assert await tracker.get(task.task_id, **_owner_kwargs()) is None


async def test_delete_expired_retries_after_persistent_delete_fails():
    agfs = _FakeAgfs()
    writer = TaskTracker(store=PersistentTaskStore(agfs))
    task = await writer.create("session_commit", **_owner_kwargs())
    await writer.start(task.task_id)
    await writer.complete(task.task_id, {})
    path = f"/local/acme/_system/tasks/alice/{task.task_id}.json"
    payload = json.loads(agfs.files[path])
    payload["updated_at"] = time.time() - writer.TTL_COMPLETED - 1
    agfs.files[path] = json.dumps(payload).encode()
    tracker = TaskTracker(store=PersistentTaskStore(agfs))
    agfs.fail_rm = True

    with pytest.raises(OSError, match="simulated delete failure"):
        await tracker.delete_expired()
    assert await tracker.get(task.task_id, **_owner_kwargs()) is not None

    agfs.fail_rm = False
    assert (await tracker.delete_expired()).deleted_count == 1

    assert await tracker.get(task.task_id, **_owner_kwargs()) is None
    assert all(call["ctx"]["disable_auto_pathlock"] == "true" for call in agfs.rm_calls)


async def test_evict_keeps_recent_completed_with_bounded_reads(monkeypatch):
    agfs = _FakeAgfs()
    writer = TaskTracker(PersistentTaskStore(agfs))
    for index in range(4):
        task = await writer.create("session_commit", task_id=f"task-{index}", **_owner_kwargs())
        await writer.complete(task.task_id, {}, **_owner_kwargs())
    tracker = TaskTracker(PersistentTaskStore(agfs))
    monkeypatch.setattr(tracker, "CLEANUP_BATCH", 2)
    for task_id in ("task-0", "task-1"):
        await tracker.get(task_id, **_owner_kwargs())
    read = create_autospec(agfs.read, side_effect=agfs.read)
    monkeypatch.setattr(agfs, "read", read)

    first = await tracker.delete_expired()
    assert first.scanned == 2 and first.deleted_count == 0
    assert read.call_count == 0
    second = await tracker.delete_expired()
    assert second.scanned == 2 and second.deleted_count == 0
    assert read.call_count == 2
    assert len(agfs.files) == 4


# ── Singleton ──


async def test_singleton():
    t1 = _set_fake_global_tracker()
    t2 = get_task_tracker()
    assert t1 is t2


async def test_singleton_reset():
    t1 = _set_fake_global_tracker()
    set_task_tracker(None)
    t2 = _set_fake_global_tracker()
    assert t1 is not t2


async def test_get_task_tracker_requires_service_initialization():
    with pytest.raises(RuntimeError, match="TaskTracker not initialized"):
        get_task_tracker()


async def test_wait_timeout_preserves_recorded_operation_result(tracker):
    set_task_tracker(tracker)
    state = {}

    async def operation():
        context = get_task_context()
        state["task_id"] = context.task_id
        await tracker.register_work(
            context.task_id,
            "child-work",
            "Semantic",
            account_id=context.account_id,
            user_id=context.user_id,
        )
        return {"status": "success", "root_uri": "resource-result"}

    with pytest.raises(DeadlineExceededError) as caught:
        await run_tracked_operation(
            "content_write",
            account_id="acme",
            user_id="alice",
            operation=operation,
            wait=True,
            timeout=0,
        )

    task = await tracker.get(state["task_id"], **_owner_kwargs())
    assert "task_id" not in caught.value.details
    assert task.status is TaskStatus.RUNNING
    assert task.result == {
        "status": "success",
        "root_uri": "resource-result",
        "task_id": state["task_id"],
    }


async def test_accept_failed_result_returns_structured_partial_result(tracker):
    set_task_tracker(tracker)
    workers = []

    async def operation():
        context = get_task_context()
        await tracker.register_work(
            context.task_id,
            "embedding-work",
            "Embedding",
            account_id=context.account_id,
            user_id=context.user_id,
        )

        async def fail_work():
            await asyncio.sleep(0)
            await tracker.mark_work_failed(
                context.task_id,
                "embedding-work",
                "embedding failed",
                account_id=context.account_id,
                user_id=context.user_id,
            )

        workers.append(asyncio.create_task(fail_work()))
        return {
            "status": "completed",
            "failed_records": 1,
            "warnings": ["Embedding queue failed during reindex: embedding failed"],
        }

    result, task_id = await run_tracked_operation(
        "admin_reindex",
        account_id="acme",
        user_id="alice",
        operation=operation,
        wait=True,
        accept_failed_result=lambda value: (
            isinstance(value, dict) and value.get("failed_records", 0) > 0
        ),
    )

    assert result["failed_records"] == 1
    task = await tracker.wait(task_id, **_owner_kwargs(), timeout=1)
    assert task.status is TaskStatus.FAILED
    assert task.result["failed_records"] == 1
    await asyncio.gather(*workers)


@pytest.mark.parametrize(
    ("raised", "expected_message"),
    [
        (TimeoutError("operation timed out"), "operation timed out"),
        (DeadlineExceededError("queue processing", 0.1), "Queue processing timed out after 0.1s"),
    ],
)
async def test_operation_timeout_marks_task_failed(tracker, raised, expected_message):
    set_task_tracker(tracker)
    state = {}

    async def operation():
        state["task_id"] = get_task_context().task_id
        raise raised

    with pytest.raises(type(raised)):
        await run_tracked_operation(
            "content_write",
            account_id="acme",
            user_id="alice",
            operation=operation,
            wait=True,
            timeout=0.1,
        )

    task = await tracker.get(state["task_id"], **_owner_kwargs())
    assert task.status is TaskStatus.FAILED
    assert task.error == expected_message


async def test_wait_false_still_finishes_top_level_operation(tracker):
    set_task_tracker(tracker)
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation():
        started.set()
        await release.wait()
        return {"status": "success", "value": 1}

    running = asyncio.create_task(
        run_tracked_operation(
            "content_write",
            account_id="acme",
            user_id="alice",
            operation=operation,
            wait=False,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not running.done()

    release.set()
    (result, task_id) = await asyncio.wait_for(running, timeout=1)
    assert result == {"status": "success", "value": 1, "task_id": task_id}
    completed = await tracker.get(task_id, **_owner_kwargs())
    assert completed.status is TaskStatus.COMPLETED
    assert completed.result == {
        "status": "success",
        "value": 1,
        "task_id": task_id,
    }


@pytest.mark.parametrize("cancel_status", ["pending", "running"])
async def test_cancel_during_operation_startup_settles_persisted_task(cancel_status):
    """Cancellation must not orphan a task whose store write has already started."""
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    class BlockingAgfs(_FakeAgfs):
        def write(self, path, data, *args, **kwargs):
            if json.loads(data)["status"] == cancel_status:
                loop.call_soon_threadsafe(started.set)
                assert release.wait(3)
            return super().write(path, data, *args, **kwargs)

    agfs = BlockingAgfs()
    tracker = TaskTracker(PersistentTaskStore(agfs))
    set_task_tracker(tracker)
    operation_called = False

    async def operation():
        nonlocal operation_called
        operation_called = True
        return {"ok": True}

    running = asyncio.create_task(
        run_tracked_operation("content_write", **_owner_kwargs(), operation=operation, wait=False)
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        running.cancel()
        await asyncio.sleep(0)
        running.cancel()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 1)

    assert not operation_called
    persisted = await TaskTracker(PersistentTaskStore(agfs)).list_tasks(**_owner_kwargs())
    assert len(persisted) == 1
    assert persisted[0].status is TaskStatus.FAILED
    assert persisted[0].error == "CancelledError"
    assert (
        await tracker.wait(persisted[0].task_id, **_owner_kwargs(), timeout=0)
    ).status is TaskStatus.FAILED


async def test_operation_start_failure_settles_created_task():
    class FailingAgfs(_FakeAgfs):
        def write(self, path, data, *args, **kwargs):
            if json.loads(data)["status"] == "running":
                raise OSError("start write failed")
            return super().write(path, data, *args, **kwargs)

    tracker = TaskTracker(PersistentTaskStore(FailingAgfs()))
    set_task_tracker(tracker)

    async def operation():
        pytest.fail("operation must not run after start fails")

    with pytest.raises(OSError, match="start write failed"):
        await run_tracked_operation(
            "content_write", **_owner_kwargs(), operation=operation, wait=False
        )

    tasks = await tracker.list_tasks(**_owner_kwargs())
    assert len(tasks) == 1
    assert tasks[0].status is TaskStatus.FAILED
    assert tasks[0].error == "start write failed"


@pytest.mark.parametrize("timeout", [0, 0.01])
async def test_wait_timeout_excludes_operation_time_for_completed_task(tracker, timeout):
    set_task_tracker(tracker)
    workers = []

    async def operation():
        await asyncio.sleep(0.02)
        context = get_task_context()
        await tracker.wait_for_task_work(
            context.task_id, exclude_work_id=context.work_id, timeout=0, **_owner_kwargs()
        )
        if timeout:
            await tracker.register_work(
                context.task_id,
                "embedding",
                "Embedding",
                account_id="acme",
                user_id="alice",
            )

            async def complete_work():
                await asyncio.sleep(0.001)
                await tracker.mark_work_done(
                    context.task_id,
                    "embedding",
                    account_id="acme",
                    user_id="alice",
                )

            workers.append(asyncio.create_task(complete_work()))
        return {"status": "success", "vector_status": "queued" if timeout else "skipped"}

    result, task_id = await run_tracked_operation(
        "content_write",
        account_id="acme",
        user_id="alice",
        operation=operation,
        wait=True,
        timeout=timeout,
        return_terminal_result=True,
    )

    assert result["status"] == "success"
    assert "task_id" not in result
    assert result["vector_status"] == ("complete" if timeout else "skipped")
    if timeout:
        assert result["queue_status"]["Embedding"]["processed"] == 1
    completed = await tracker.wait(task_id, **_owner_kwargs(), timeout=0)
    assert completed.status is TaskStatus.COMPLETED
    assert completed.result["status"] == "success"
    assert completed.result["task_id"] == task_id
    await asyncio.gather(*workers)


async def test_multiple_queue_waits_share_budget_and_leave_task_pollable(tracker):
    set_task_tracker(tracker)
    state = {}

    async def operation():
        task_id = get_task_context().task_id
        state["task_id"] = task_id
        await tracker.register_work(task_id, "first", "Semantic", **_owner_kwargs())
        # Exhaust the budget on a caught wait, then make the first Work complete.
        with pytest.raises(TimeoutError):
            await tracker.wait_for_work(task_id, ["first"], timeout=0.01)
        await tracker.mark_work_done(task_id, "first", **_owner_kwargs())
        await tracker.register_work(task_id, "second", "Embedding", **_owner_kwargs())
        with pytest.raises(TimeoutError):
            await tracker.wait_for_task_work(task_id, timeout=60)
        return {"status": "success"}

    with pytest.raises(DeadlineExceededError) as caught:
        await asyncio.wait_for(
            run_tracked_operation(
                "content_write",
                **_owner_kwargs(),
                operation=operation,
                wait=True,
                timeout=0.01,
            ),
            timeout=1,
        )
    assert "task_id" not in caught.value.details
    task = await tracker.get(state["task_id"], **_owner_kwargs())
    assert task.status is TaskStatus.RUNNING
    assert task.error is None
    await tracker.mark_work_done(task.task_id, "second", **_owner_kwargs())
    assert (
        await tracker.wait(task.task_id, **_owner_kwargs(), timeout=0)
    ).status is TaskStatus.COMPLETED


async def test_persistent_store_cross_tracker_visibility_after_cancelled_write(monkeypatch):
    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker1 = TaskTracker(store=store)
    tracker2 = TaskTracker(store=store)

    task = await tracker1.create("session_commit", resource_id="sess-123", **_owner_kwargs())
    await tracker1.start(task.task_id, account_id="acme", user_id="alice")
    entered, release = threading.Event(), threading.Event()
    write = agfs.write

    def blocked_write(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return write(*args, **kwargs)

    monkeypatch.setattr(agfs, "write", blocked_write)
    completing = asyncio.create_task(
        tracker1.complete(task.task_id, {"ok": True}, **_owner_kwargs())
    )
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        completing.cancel()
        await asyncio.sleep(0)
        assert not completing.done()
        assert (await tracker2.get(task.task_id, **_owner_kwargs())).status is TaskStatus.RUNNING
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await completing
    finally:
        release.set()
        await asyncio.gather(completing, return_exceptions=True)

    loaded = await tracker2.get(task.task_id, account_id="acme", user_id="alice")

    assert loaded is not None
    assert loaded.status == TaskStatus.COMPLETED
    assert loaded.result == {"ok": True}
    restored = await TaskTracker(PersistentTaskStore(agfs)).get(task.task_id, **_owner_kwargs())
    assert restored.status is TaskStatus.COMPLETED
    assert restored.result == loaded.result


@pytest.mark.parametrize(
    "extra",
    [
        {
            "operation_id": "operation-1",
            "parent_task_id": "parent-1",
            "attempt_number": 2,
            "error_info": {"category": "network", "retryable": True},
        },
        {"future_field": {"values": [1, 2]}, "_extra_fields": {"reserved": True}},
    ],
)
async def test_persistent_task_extensions_survive_updates(extra):
    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    writer = TaskTracker(store=store)
    task = await writer.create("session_commit", **_owner_kwargs())
    path = f"/local/acme/_system/tasks/alice/{task.task_id}.json"
    payload = json.loads(agfs.files[path])
    payload.update(extra)
    agfs.files[path] = json.dumps(payload).encode()
    original = agfs.files[path]

    reader = TaskTracker(store=PersistentTaskStore(agfs))
    loaded = await reader.get(task.task_id, **_owner_kwargs())
    assert loaded is not None
    assert loaded.status == TaskStatus.PENDING
    assert not (set(extra) & set(loaded.to_dict()))
    assert len(await reader.list_tasks(**_owner_kwargs())) == 1
    assert await reader.get(task.task_id, account_id="acme", user_id="bob") is None
    assert agfs.files[path] == original

    await reader.start(task.task_id, **_owner_kwargs())
    await reader.complete(task.task_id, {"ok": True}, **_owner_kwargs())
    saved = json.loads(agfs.files[path])
    assert saved["status"] == "completed"
    assert saved["result"] == {"ok": True}
    assert {key: saved[key] for key in extra} == extra
    reloaded = await TaskTracker(store=PersistentTaskStore(agfs)).get(
        task.task_id, **_owner_kwargs()
    )
    assert reloaded is not None
    assert reloaded.status == TaskStatus.COMPLETED
    assert {key: _task_to_payload(reloaded)[key] for key in extra} == extra


async def test_task_extension_payload_is_defensively_copied():
    payload = {
        "task_id": "task-1",
        "task_type": "session_commit",
        "status": "pending",
        "future_field": {"values": [1]},
        "meta": {"values": [2]},
    }
    original = deepcopy(payload)
    record = _task_from_payload(payload)
    record._extra_fields["future_field"]["values"].append(3)
    record.meta["values"].append(4)
    assert payload == original
    saved = _task_to_payload(record)
    saved["future_field"]["values"].append(5)
    assert record._extra_fields["future_field"]["values"] == [1, 3]
    record._extra_fields["status"] = "failed"
    assert _task_to_payload(record)["status"] == "pending"


@pytest.mark.parametrize("status", ["unknown", None])
async def test_task_extensions_do_not_hide_invalid_status(status):
    with pytest.raises(ValueError):
        _task_from_payload(
            {"task_id": "task-1", "task_type": "session_commit", "status": status, "extra": 1}
        )


async def test_persistent_store_writes_task_record_json():
    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store=store)

    task = await tracker.create(
        "add_resource",
        resource_id="viking://resources/demo",
        auth={"provider": "feishu", "access_token": "secret-token"},
        **_owner_kwargs(),
    )

    raw = agfs.files[f"/local/acme/_system/tasks/alice/{task.task_id}.json"]
    payload = json.loads(raw.decode("utf-8"))

    assert agfs.write_calls[-1]["ctx"]["disable_auto_pathlock"] == "true"
    assert payload["task_id"] == task.task_id
    assert payload["task_type"] == "add_resource"
    assert payload["account_id"] == "acme"
    assert payload["user_id"] == "alice"
    assert payload["stage"] is None
    assert payload["auth"] == {
        "provider": "feishu",
        "access_token": "secret-token",
    }
    assert "schema_version" not in payload

    await tracker.complete(task.task_id, {"ok": True}, **_owner_kwargs())
    terminal_payload = json.loads(
        agfs.files[f"/local/acme/_system/tasks/alice/{task.task_id}.json"].decode("utf-8")
    )
    assert terminal_payload["auth"] == {}
    assert all(call["ctx"]["disable_auto_pathlock"] == "true" for call in agfs.write_calls)


async def test_persistent_store_survives_tracker_reset():
    agfs = _FakeAgfs()
    tracker1 = TaskTracker(store=PersistentTaskStore(agfs))
    task = await tracker1.create(
        "session_commit",
        resource_id="sess-123",
        auth={"provider": "feishu", "access_token": "secret-token"},
        **_owner_kwargs(),
    )
    await tracker1.start(task.task_id, account_id="acme", user_id="alice")

    tracker2 = TaskTracker(store=PersistentTaskStore(agfs))
    loaded = await tracker2.get(task.task_id, account_id="acme", user_id="alice")

    assert loaded is not None
    assert loaded.status == TaskStatus.RUNNING
    assert loaded.auth == {}
    assert await tracker2.get_task_auth(task.task_id, **_owner_kwargs()) == {
        "provider": "feishu",
        "access_token": "secret-token",
    }


async def test_persistent_store_ignores_existing_task_dirs():
    agfs = _FakeAgfsExistingDir()
    tracker = TaskTracker(store=PersistentTaskStore(agfs))

    first = await tracker.create("session_commit", resource_id="sess-1", **_owner_kwargs())
    first_mkdir_calls = list(agfs.mkdir_calls)
    second = await tracker.create("session_commit", resource_id="sess-2", **_owner_kwargs())

    assert first.task_id != second.task_id
    assert agfs.files[f"/local/acme/_system/tasks/alice/{first.task_id}.json"]
    assert agfs.files[f"/local/acme/_system/tasks/alice/{second.task_id}.json"]
    assert [call["path"] for call in first_mkdir_calls] == [
        "/local/acme",
        "/local/acme/_system",
        "/local/acme/_system/tasks",
        "/local/acme/_system/tasks/alice",
    ]
    assert agfs.mkdir_calls == first_mkdir_calls
    assert all(call["ctx"]["disable_auto_pathlock"] == "true" for call in agfs.write_calls)


async def test_create_requires_owner(tracker: TaskTracker):
    with pytest.raises(TypeError):
        await tracker.create("session_commit", resource_id="sess-123")


async def test_create_if_no_running_requires_owner(tracker: TaskTracker):
    with pytest.raises(TypeError):
        await tracker.create_if_no_running("reindex", "viking://resources/demo")


async def test_create_rejects_blank_owner_values(tracker: TaskTracker):
    with pytest.raises(ValueError, match="Task ownership requires"):
        await tracker.create(
            "session_commit",
            resource_id="sess-123",
            account_id="",
            user_id="alice",
        )


async def test_session_service_get_commit_task_is_owner_scoped():
    tracker = _set_fake_global_tracker()
    task = await tracker.create("session_commit", resource_id="sess-123", **_owner_kwargs())
    service = SessionService()

    owner_result = await service.get_commit_task(task.task_id, _make_ctx())
    other_result = await service.get_commit_task(task.task_id, _make_ctx(user_id="bob"))

    assert owner_result is not None
    assert owner_result["task_id"] == task.task_id
    assert owner_result["resource_id"] == "sess-123"
    assert other_result is None


async def test_session_service_get_commit_task_also_filters_account():
    tracker = _set_fake_global_tracker()
    task = await tracker.create("session_commit", resource_id="sess-123", **_owner_kwargs())
    service = SessionService()

    other_account_result = await service.get_commit_task(
        task.task_id,
        _make_ctx(account_id="other-acme", user_id="alice"),
    )

    assert other_account_result is None


async def test_feishu_response_checkpoint_survives_reload(tracker):
    task = await tracker.create("add_resource", **_owner_kwargs())
    await tracker.patch_task(
        task.task_id,
        TaskPatch(meta={"feishu_responses": {"nested/doc": "response-1"}}),
        **_owner_kwargs(),
    )
    restored = TaskTracker(store=tracker._store)
    record = await restored.get(task.task_id, **_owner_kwargs())
    assert record.meta["feishu_responses"] == {"nested/doc": "response-1"}


async def test_execution_events_survive_restart_and_preserve_reported_facts():
    store = PersistentTaskStore(_FakeAgfs())
    tracker = TaskTracker(store=store)
    owner = _owner_kwargs()
    task = await tracker.create("add_resource", **owner)
    await tracker.start(task.task_id, stage="parsing", **owner)
    await tracker.start(task.task_id, stage="parsing", **owner)
    await tracker.record_event(task.task_id, "waiting_for_descendants", operation="work-1", **owner)
    await tracker.fail(task.task_id, "provider rejected sk-testsecret123", **owner)
    before = (await tracker.get_with_events(task.task_id, **owner)).to_dict(include_events=True)
    restored = (await TaskTracker(store=store).get_with_events(task.task_id, **owner)).to_dict(
        include_events=True
    )
    assert restored == before
    events = restored["execution_events"]["items"]
    assert [event["kind"] for event in events] == [
        "created",
        "status_changed",
        "stage_changed",
        "waiting_for_descendants",
        "error_recorded",
        "status_changed",
    ]
    assert [event["seq"] for event in events] == list(range(1, 7))
    assert events[-1]["status"] == "failed"
    assert events[-1]["stage"] == "parsing"
    assert "[REDACTED]" in events[-2]["error"]
    assert "testsecret123" not in json.dumps(restored)
    assert "execution_events" not in (await tracker.get(task.task_id, **owner)).to_dict()


@pytest.mark.parametrize("outcome", ["complete", "fail"])
async def test_terminal_event_waits_for_owned_work(tracker, outcome):
    owner = _owner_kwargs()
    task = await tracker.create("add_resource", **owner)
    await tracker.start(task.task_id, **owner)
    await tracker.register_work(task.task_id, "child-work", "Semantic", **owner)
    await getattr(tracker, outcome)(
        task.task_id, {"done": True} if outcome == "complete" else "error", **owner
    )
    snapshot = await tracker.get_with_events(task.task_id, **owner)
    assert snapshot.status == TaskStatus.RUNNING
    await tracker.mark_work_done(task.task_id, "child-work", **owner)
    snapshot = await tracker.get_with_events(task.task_id, **owner)
    last = snapshot.execution_events["items"][-1]
    assert last["kind"] == "status_changed"
    assert last["status"] == ("completed" if outcome == "complete" else "failed")


async def test_legacy_tasks_do_not_get_invented_history():
    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    task = await TaskTracker(store=store).create("session_commit", **_owner_kwargs())
    path = f"/local/acme/_system/tasks/alice/{task.task_id}.json"
    payload = json.loads(agfs.files[path])
    payload.pop("execution_events")
    agfs.files[path] = json.dumps(payload).encode()
    reader = TaskTracker(store=PersistentTaskStore(agfs))
    assert (await reader.get(task.task_id, **_owner_kwargs())).execution_events is None
    await reader.start(task.task_id, **_owner_kwargs())
    history = (await reader.get_with_events(task.task_id, **_owner_kwargs())).execution_events
    assert history["started_mid_task"] is True
    assert [event["kind"] for event in history["items"]] == ["status_changed"]


@pytest.mark.parametrize("stage_prefix", ["stage", "阶段" * 64])
async def test_event_history_is_bounded_and_reports_truncation(tracker, stage_prefix):
    from openviking.service.task_events import MAX_TASK_EVENT_BYTES, MAX_TASK_EVENTS

    task = await tracker.create("add_resource", **_owner_kwargs())
    for index in range(80):
        await tracker.patch_task(
            task.task_id,
            TaskPatch(stage=f"{index}:{stage_prefix}"),
            **_owner_kwargs(),
        )
    await tracker.complete(task.task_id, {"done": True}, **_owner_kwargs())
    history = (await tracker.get_with_events(task.task_id, **_owner_kwargs())).execution_events
    assert len(history["items"]) <= MAX_TASK_EVENTS
    assert len(json.dumps(history).encode()) <= MAX_TASK_EVENT_BYTES
    assert history["dropped_count"] + len(history["items"]) == 82
    assert history["items"][0]["seq"] == history["dropped_count"] + 1
    assert history["items"][-1]["status"] == "completed"


async def test_process_events_respect_owner_and_terminal_boundaries(tracker):
    task = await tracker.create("add_resource", **_owner_kwargs())
    await tracker.record_event(
        task.task_id, "waiting_for_descendants", account_id="acme", user_id="bob"
    )
    assert (
        await tracker.get_with_events(task.task_id, **_owner_kwargs())
    ).execution_events == task.execution_events
    with pytest.raises(ValueError, match="Unknown task process event"):
        await tracker.record_event(task.task_id, "completed", **_owner_kwargs())
    with pytest.raises(ValueError, match="operation"):
        await tracker.record_event(
            task.task_id, "waiting_for_descendants", operation="Bearer secret", **_owner_kwargs()
        )
    await tracker.cancel(task.task_id, **_owner_kwargs())
    cancelled = await tracker.get_with_events(task.task_id, **_owner_kwargs())
    assert [event["status"] for event in cancelled.execution_events["items"]] == [
        "pending",
        "cancelling",
        "cancelled",
    ]
    await tracker.record_event(task.task_id, "waiting_for_descendants", **_owner_kwargs())
    assert (
        await tracker.get_with_events(task.task_id, **_owner_kwargs())
    ).execution_events == cancelled.execution_events


async def test_processing_time_persists_and_restored_active_tasks_are_unknown(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("openviking.service.task_store.time.time", lambda: now[0])
    agfs = _FakeAgfs()
    tracker = TaskTracker(PersistentTaskStore(agfs))
    task = await tracker.create("add_resource", **_owner_kwargs())
    await tracker.start(task.task_id, **_owner_kwargs())
    now[0] = 10
    await tracker.register_running_task(task.task_id, **_owner_kwargs())
    now[0] = 13
    assert (await tracker.get(task.task_id)).processing_seconds == 3
    # A stage patch neither resets nor stops the processing clock.
    await tracker.patch_task(task.task_id, TaskPatch(stage="semantic"), **_owner_kwargs())
    restarted = TaskTracker(store=PersistentTaskStore(agfs))
    assert (await restarted.get(task.task_id, **_owner_kwargs())).processing_seconds is None
    # Waiting, I/O, and backoff after a worker claims the task remain part of
    # worker-occupied wall time.
    now[0] = 30
    assert (await tracker.get(task.task_id)).processing_seconds == 20
    now[0] = 32
    await tracker.complete(task.task_id, result={"ok": True}, **_owner_kwargs())
    await tracker.unregister_running_task(task.task_id)
    finished = await tracker.get(task.task_id)
    assert finished.status == TaskStatus.COMPLETED
    assert finished.to_dict()["processing_seconds"] == 22
    restored = await TaskTracker(store=PersistentTaskStore(agfs)).get(
        task.task_id, **_owner_kwargs()
    )
    assert restored.processing_seconds == 22
