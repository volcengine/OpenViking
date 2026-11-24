"""Expiry retries and executor credentials for saved Compile configurations."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.providers import LocalFileProvider
from openviking.scheduling.expiry import CallbackRequest, CallbackStatus, ItemIdentity
from openviking.server.identity import RequestContext, ResolvedIdentity, Role
from openviking.service.compile_scheduler import (
    CompileScheduler,
    _ExpiryHandler,
    current_slot,
    next_slot,
)
from openviking_cli.exceptions import FailedPreconditionError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier

COMPILE_NAME = "daily-Wiki_1"
ACCOUNT = "account"
OWNER = "creator"


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
        resolve.assert_awaited_with(caller.api_key)
    resolve.return_value = ResolvedIdentity(Role.USER, ACCOUNT, "other")
    with pytest.raises(FailedPreconditionError) as exc:
        await scheduler._executor(record)
    assert exc.value.details["reason"] == "COMPILE_CREDENTIAL_INVALID"


def test_schedule_uses_saved_timezone_across_dst():
    schedule = {
        "frequency": "daily",
        "timezone": "America/Los_Angeles",
        "hour": 9,
        "minute": 30,
    }
    now = datetime.fromisoformat("2026-03-08T17:00:00+00:00")

    current = current_slot(schedule, now)
    following = next_slot(schedule, now)

    assert current.isoformat() == "2026-03-08T09:30:00-07:00"
    assert following.isoformat() == "2026-03-09T09:30:00-07:00"


def test_schedule_uses_first_occurrence_of_fall_back_time():
    schedule = {
        "frequency": "daily",
        "timezone": "America/Los_Angeles",
        "hour": 1,
        "minute": 30,
    }
    now = datetime.fromisoformat("2026-11-01T09:45:00+00:00")

    current = current_slot(schedule, now)
    following = next_slot(schedule, now)

    assert current.isoformat() == "2026-11-01T01:30:00-07:00"
    assert following.isoformat() == "2026-11-02T01:30:00-08:00"
