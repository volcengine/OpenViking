"""Expiry retries and durable slot recovery for saved Compile configurations."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.scheduling.expiry import CallbackRequest, CallbackStatus, ItemIdentity
from openviking.service.compile_scheduler import TZ, CompileScheduler, _ExpiryHandler
from openviking.service.compile_targets import CompileLifecycle, new_record
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_domain import TaskRecord
from openviking.storage.queuefs import QueueManager
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError

COMPILE_ID = "cpt_" + "a" * 32
ACCOUNT = "account"
PARAMS = {
    "from": ["viking://resources/source"],
    "to": "viking://resources/output",
    "skill": "viking://system/compile/example-skills/llm-wiki",
    "instruction": None,
    "args": {},
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("compile_id", "exists", "lookup_error", "submission_error", "expected"),
    [
        ("../state", True, None, None, CallbackStatus.COMPLETED),
        (COMPILE_ID, False, None, None, CallbackStatus.COMPLETED),
        (COMPILE_ID, True, OSError("storage unavailable"), None, CallbackStatus.RETRY),
        (COMPILE_ID, True, None, NotFoundError("source"), CallbackStatus.RETRY),
        (
            COMPILE_ID,
            True,
            None,
            InvalidArgumentError("target must be a directory"),
            CallbackStatus.RETRY,
        ),
        (COMPILE_ID, True, None, None, CallbackStatus.COMPLETED),
    ],
)
async def test_callback_retries_submission_failures(
    compile_id, exists, lookup_error, submission_error, expected
):
    store = SimpleNamespace(
        get=AsyncMock(return_value={} if exists else None, side_effect=lookup_error)
    )
    scheduler = SimpleNamespace(
        _store=store,
        trigger_if_due=AsyncMock(return_value="submitted", side_effect=submission_error),
    )
    result = await _ExpiryHandler(scheduler).handle(
        CallbackRequest(ItemIdentity("compile_target", ACCOUNT, "_system", compile_id))
    )
    assert result.status == expected
    if compile_id != COMPILE_ID:
        store.get.assert_not_awaited()
        scheduler.trigger_if_due.assert_not_awaited()
    elif not exists or lookup_error:
        scheduler.trigger_if_due.assert_not_awaited()
    else:
        scheduler.trigger_if_due.assert_awaited_once_with(ACCOUNT, COMPILE_ID)
    assert result.reason == (
        "trigger_failed"
        if expected == CallbackStatus.RETRY
        else "submitted"
        if compile_id == COMPILE_ID and exists
        else "not_found"
    )


class LockedConfiguration:
    """Publish state atomically while preserving run writes across an interrupted submission."""

    def __init__(self, state):
        self.state = deepcopy(state)
        self.runs = {}
        self.locked = False

    @asynccontextmanager
    async def edit(self, account_id, compile_id):
        assert (account_id, compile_id) == (ACCOUNT, COMPILE_ID)
        assert not self.locked
        self.locked = True
        draft = deepcopy(self.state)
        try:
            yield draft
            self.state = draft
        finally:
            self.locked = False

    async def get_run(self, account_id, compile_id, task_id):
        assert self.locked
        return self.runs.get(task_id)

    async def put_run(self, account_id, compile_id, run):
        assert self.locked
        self.runs[run["task_id"]] = deepcopy(run)


@pytest.mark.asyncio
async def test_slot_recovery_keeps_accepted_owner_after_executor_change(monkeypatch):
    due = datetime(2026, 11, 5, 10, 0, tzinfo=TZ)
    schedule = {"frequency": "daily", "hour": 10, "minute": 0}
    record = new_record(
        {**PARAMS, "auto_update": True, "update_schedule": schedule},
        {"executor": {"account_id": ACCOUNT, "user_id": "replacement-user"}},
    )
    record["next_triggered_slot"] = due.isoformat()
    store = LockedConfiguration(record)
    accepted = TaskRecord(
        task_id="cmp_" + "b" * 32,
        task_type="compile",
        account_id=ACCOUNT,
        user_id="accepted-user",
        created_at=due.timestamp(),
        meta={
            "request": {
                **PARAMS,
                "compile": {
                    "compile_id": COMPILE_ID,
                    "trigger": "scheduled",
                    "scheduled_slot": due.isoformat(),
                    "auto_update": True,
                    "update_schedule": schedule,
                },
            }
        },
    )

    async def durable_tasks(account_id, *, task_type):
        assert store.locked
        assert (account_id, task_type) == (ACCOUNT, "compile")
        return [accepted]

    task_store = SimpleNamespace(list_durable=AsyncMock(side_effect=durable_tasks))
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: SimpleNamespace(_store=task_store),
    )
    enqueue = AsyncMock(side_effect=[OSError("queue unavailable"), None])
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager", lambda: SimpleNamespace(enqueue=enqueue)
    )
    service = SimpleNamespace(
        lifecycle=CompileLifecycle(store, None),
        _tasks=ExternalTaskService(),
        create=AsyncMock(),
    )
    scheduler = CompileScheduler(service, store)
    scheduler._executor = AsyncMock(side_effect=AssertionError("must retain accepted owner"))
    scheduler._expiry = SimpleNamespace(report=AsyncMock(), unregister=AsyncMock())

    with pytest.raises(OSError, match="queue unavailable"):
        await scheduler.trigger_if_due(ACCOUNT, COMPILE_ID, due)
    assert store.state == record
    assert len(store.runs) == 1

    assert await scheduler.trigger_if_due(ACCOUNT, COMPILE_ID, due) == "submitted"
    assert store.state["task_first_create_time"] == accepted.created_at
    assert store.state["last_run_time"] == accepted.created_at
    assert store.state["last_triggered_slot"] == due.isoformat()
    assert store.state["next_triggered_slot"] == (due + timedelta(days=1)).isoformat()
    assert store.runs[accepted.task_id]["actor"] == "accepted-user"
    assert "scheduled_slot" not in str(store.runs[accepted.task_id])
    assert len(store.runs) == 1
    service.create.assert_not_awaited()
    scheduler._executor.assert_not_awaited()
    task_store.list_durable.assert_awaited()
    enqueue.assert_awaited_with(
        QueueManager.EXTERNAL_TASK,
        {"task_id": accepted.task_id, "account_id": ACCOUNT, "user_id": "accepted-user"},
    )
    assert await scheduler.trigger_if_due(ACCOUNT, COMPILE_ID, due) == "not_due"
    assert enqueue.await_count == 2
