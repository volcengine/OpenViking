"""Expiry retries and durable slot recovery for saved Compile configurations."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.providers import LocalFileProvider
from openviking.scheduling.expiry import CallbackRequest, CallbackStatus, ItemIdentity
from openviking.server.identity import RequestContext, ResolvedIdentity, Role
from openviking.service.compile_scheduler import TZ, CompileScheduler, _ExpiryHandler
from openviking.service.compile_targets import CompileLifecycle, new_record
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_domain import TaskRecord
from openviking.storage.queuefs import QueueManager
from openviking_cli.exceptions import FailedPreconditionError, InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier

COMPILE_NAME = "daily-Wiki_1"
ACCOUNT = "account"
OWNER = "creator"
PARAMS = {
    "from": ["viking://resources/source"],
    "to": "viking://resources/output",
    "skill": "viking://system/compile/example-skills/llm-wiki",
    "instruction": None,
    "args": {},
}


@pytest.mark.parametrize(
    ("compile_name", "exists", "lookup_error", "submission_error", "expected", "reason"),
    [
        ("../state", True, None, None, CallbackStatus.COMPLETED, "not_found"),
        (COMPILE_NAME, False, None, None, CallbackStatus.COMPLETED, "not_found"),
        (
            COMPILE_NAME,
            True,
            OSError("storage unavailable"),
            None,
            CallbackStatus.RETRY,
            "trigger_failed",
        ),
        (COMPILE_NAME, True, None, NotFoundError("source"), CallbackStatus.RETRY, "trigger_failed"),
        (
            COMPILE_NAME,
            True,
            None,
            InvalidArgumentError("target must be a directory"),
            CallbackStatus.RETRY,
            "trigger_failed",
        ),
        (COMPILE_NAME, True, None, None, CallbackStatus.COMPLETED, "submitted"),
    ],
)
async def test_callback_retries_submission_failures(
    compile_name, exists, lookup_error, submission_error, expected, reason
):
    store = SimpleNamespace(
        get=AsyncMock(return_value={} if exists else None, side_effect=lookup_error)
    )
    scheduler = SimpleNamespace(
        _store=store,
        trigger_if_due=AsyncMock(return_value="submitted", side_effect=submission_error),
    )
    result = await _ExpiryHandler(scheduler).handle(
        CallbackRequest(ItemIdentity("compile_target", ACCOUNT, OWNER, compile_name))
    )
    assert (result.status, result.reason) == (expected, reason)
    if compile_name == COMPILE_NAME:
        store.get.assert_awaited_once_with(ACCOUNT, OWNER, COMPILE_NAME)
    else:
        store.get.assert_not_awaited()
    if reason == "not_found" or lookup_error:
        scheduler.trigger_if_due.assert_not_awaited()
    else:
        scheduler.trigger_if_due.assert_awaited_once_with(ACCOUNT, OWNER, COMPILE_NAME)


async def test_scheduled_credential_uses_current_key_identity():
    scheduler = CompileScheduler(None, None)
    scheduler.credential_encryptor = FileEncryptor(LocalFileProvider(root_key="ab" * 32))
    caller = RequestContext(user=UserIdentifier(ACCOUNT, OWNER), role=Role.USER, api_key="test-key")
    credential = await scheduler.save_credential(caller)
    executor = {
        "account_id": ACCOUNT,
        "user_id": OWNER,
        "role": Role.USER,
        "group_ids": [],
    }
    record = {"execution_context": {"executor": executor, "credential": credential}}

    resolve = AsyncMock()
    scheduler.set_identity_resolver(resolve, AsyncMock(return_value=["reviewers"]))
    for role in (Role.ROOT, Role.USER, Role.ADMIN, Role.GLOBAL):
        identity = (
            ResolvedIdentity(role) if role == Role.ROOT else ResolvedIdentity(role, ACCOUNT, OWNER)
        )
        resolve.return_value = identity
        ctx, _ = await scheduler._executor(record)
        assert ctx.user == caller.user and ctx.role == (Role.USER if role == Role.ROOT else role)
        assert ctx.group_ids == ("reviewers",) and not ctx.bypass_acl
    resolve.return_value = ResolvedIdentity(Role.USER, ACCOUNT, "other")
    with pytest.raises(FailedPreconditionError):
        await scheduler._executor(record)


class LockedConfiguration:
    """Publish state atomically while preserving run writes across an interrupted submission."""

    def __init__(self, state):
        self.state = deepcopy(state)
        self.runs = {}
        self.locked = False

    @asynccontextmanager
    async def edit(self, account_id, owner_user_id, compile_name):
        assert (account_id, owner_user_id, compile_name) == (ACCOUNT, OWNER, COMPILE_NAME)
        assert not self.locked
        self.locked = True
        draft = deepcopy(self.state)
        try:
            yield draft
            self.state = draft
        finally:
            self.locked = False

    async def get_run(self, account_id, owner_user_id, compile_name, task_id):
        assert (account_id, owner_user_id, compile_name) == (ACCOUNT, OWNER, COMPILE_NAME)
        assert self.locked
        return self.runs.get(task_id)

    async def put_run(self, account_id, owner_user_id, compile_name, run):
        assert (account_id, owner_user_id, compile_name) == (ACCOUNT, OWNER, COMPILE_NAME)
        assert self.locked
        self.runs[run["task_id"]] = deepcopy(run)


async def test_slot_recovery_keeps_accepted_owner_after_executor_change(monkeypatch):
    compile_name = COMPILE_NAME
    due = datetime(2026, 11, 5, 10, 0, tzinfo=TZ)
    schedule = {"frequency": "daily", "hour": 10, "minute": 0}
    record = new_record(
        {**PARAMS, "auto_update": True, "update_schedule": schedule},
        {
            "owner_user_id": OWNER,
            "executor": {"account_id": ACCOUNT, "user_id": "replacement-user"},
        },
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
                    "compile_name": compile_name,
                    "owner_user_id": OWNER,
                    "trigger": "scheduled",
                    "scheduled_slot": due.isoformat(),
                    "auto_update": True,
                    "update_schedule": schedule,
                },
            }
        },
    )
    unrelated = deepcopy(accepted)
    unrelated.task_id = "cmp_" + "d" * 32
    unrelated.meta["request"]["compile"]["owner_user_id"] = "another-creator"

    async def durable_tasks(account_id, *, task_type):
        assert store.locked
        assert (account_id, task_type) == (ACCOUNT, "compile")
        return [unrelated, accepted]

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
    service._tasks._task_auth = AsyncMock(return_value={})
    scheduler = CompileScheduler(service, store)
    scheduler._executor = AsyncMock(side_effect=AssertionError("must retain accepted owner"))
    scheduler._expiry = SimpleNamespace(report=AsyncMock(), unregister=AsyncMock())

    with pytest.raises(OSError, match="queue unavailable"):
        await scheduler.trigger_if_due(ACCOUNT, OWNER, compile_name, due)
    assert store.state == record
    assert len(store.runs) == 1

    assert await scheduler.trigger_if_due(ACCOUNT, OWNER, compile_name, due) == "submitted"
    assert store.state["task_first_create_time"] == accepted.created_at
    assert store.state["last_run_time"] == accepted.created_at
    assert store.state["last_triggered_slot"] == due.isoformat()
    assert store.state["next_triggered_slot"] == (due + timedelta(days=1)).isoformat()
    assert store.runs[accepted.task_id]["actor"] == "accepted-user"
    assert len(store.runs) == 1
    service.create.assert_not_awaited()
    enqueue.assert_awaited_with(
        QueueManager.EXTERNAL_TASK,
        {"task_id": accepted.task_id, "account_id": ACCOUNT, "user_id": "accepted-user"},
    )
    assert await scheduler.trigger_if_due(ACCOUNT, OWNER, compile_name, due) == "not_due"
    assert enqueue.await_count == 2
    store.state["auto_update"] = False
    await scheduler.reschedule(ACCOUNT, OWNER, compile_name, store.state)
    identity = ItemIdentity("compile_target", ACCOUNT, OWNER, compile_name)
    scheduler._expiry.unregister.assert_awaited_once_with(identity)
    assert scheduler._expiry.report.call_args.args[0].identity == identity
