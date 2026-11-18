"""Receipt validation cannot turn missing evidence into successful processing."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.task_domain import TaskRecord, TaskStatus
from openviking.utils.compile_publication import (
    MAX_RECEIPTS,
    PROTOCOL,
    PublicationReceipts,
    publication_evidence,
    resolve_publication,
)
from openviking_cli.session.user_id import UserIdentifier

TARGET = "viking://resources/output"
RECEIPT = {"task_id": "write-exact", "uri": TARGET, "kind": "content_batch_write"}


def evidence(*rows, **extra):
    return {"protocol": PROTOCOL, "mode": "async", "receipts": list(rows), **extra}


def test_collector_preserves_no_writes_and_missing_receipt_boundaries():
    collector = PublicationReceipts()
    assert collector.snapshot()["state"] == "not_requested"
    assert collector.snapshot(synchronous=True)["state"] == "complete"
    assert collector.snapshot(uncertain=True)["state"] == "unknown"
    collector.add(None, TARGET, "content_batch_write")
    assert collector.snapshot()["state"] == "unknown"
    assert collector.snapshot()["incomplete"] is True


@pytest.mark.parametrize("value", [None, {}, {"protocol": "future"}, {"state": "complete"}])
def test_legacy_cannot_claim_proven_success(value):
    assert publication_evidence(value)["protocol"] is None
    assert publication_evidence(value)["state"] == "unknown"


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        {**RECEIPT, "task_id": "../bad"},
        {**RECEIPT, "uri": "viking://resources/../bad"},
        {**RECEIPT, "kind": []},
        {**RECEIPT, "uri": "https://example.invalid/file"},
    ],
)
def test_invalid_receipts_fail_closed_without_echoing_content(row):
    normalized = publication_evidence(evidence(row, error="private detail", state="complete"))
    assert normalized["state"] == "unknown" and normalized["incomplete"]
    assert "private detail" not in str(normalized)


def test_producer_conclusion_is_ignored_and_duplicate_task_ids_are_checked():
    assert publication_evidence(evidence(RECEIPT, state="complete"))["state"] == "pending"
    assert publication_evidence(evidence(RECEIPT, RECEIPT))["receipts"] == [RECEIPT]
    assert publication_evidence(evidence(RECEIPT, {**RECEIPT, "uri": TARGET + "/other"}))[
        "incomplete"
    ]


def test_bounded_receipts_mark_overflow_instead_of_claiming_a_complete_subset():
    collector = PublicationReceipts()
    for index in range(MAX_RECEIPTS + 1):
        collector.add(f"write-{index}", TARGET, "content_batch_write")
    normalized = collector.snapshot()
    assert len(normalized["receipts"]) == MAX_RECEIPTS
    assert normalized["incomplete"] and normalized["state"] == "unknown"


async def test_resolution_is_durable_scoped_and_bounded_and_preserves_read_errors():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    active = peak = 0

    async def read(task_id, **scope):
        nonlocal active, peak
        assert scope == {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            return TaskRecord(
                task_id=task_id,
                task_type="content_batch_write",
                resource_id=TARGET,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                status=TaskStatus.COMPLETED,
            )
        finally:
            active -= 1

    tracker = SimpleNamespace(_store=SimpleNamespace(get_durable_task=read), get=AsyncMock())
    kwargs = {
        "tracker": tracker,
        "ctx": ctx,
        "account_id": ctx.account_id,
        "user_id": ctx.user.user_id,
        "target": TARGET,
    }
    result = await resolve_publication(
        evidence(*[{**RECEIPT, "task_id": f"write-{i}"} for i in range(30)]), **kwargs
    )
    assert result["state"] == "complete" and peak == 8 and active == 0
    tracker.get.assert_not_awaited()
    tracker._store.get_durable_task = AsyncMock(side_effect=OSError("durable store offline"))
    with pytest.raises(OSError, match="durable store offline"):
        await resolve_publication(evidence(RECEIPT), **kwargs)


async def test_mixed_pending_and_failed_waits_for_all_before_terminal_failure():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    tasks = {
        "write-exact": TaskRecord(
            task_id="write-exact",
            task_type="content_batch_write",
            resource_id=TARGET,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            status=TaskStatus.FAILED,
        ),
        "write-other": TaskRecord(
            task_id="write-other",
            task_type="content_batch_write",
            resource_id=TARGET,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            status=TaskStatus.CANCELLING,
        ),
    }

    async def read(task_id, **scope):
        return deepcopy(tasks[task_id])

    tracker = SimpleNamespace(_store=SimpleNamespace(get_durable_task=read))
    kwargs = {
        "tracker": tracker,
        "ctx": ctx,
        "account_id": ctx.account_id,
        "user_id": ctx.user.user_id,
        "target": TARGET,
    }
    value = evidence(RECEIPT, {**RECEIPT, "task_id": "write-other"})
    assert (await resolve_publication(value, **kwargs))["state"] == "pending"
    tasks["write-other"].status = TaskStatus.CANCELLED
    assert (await resolve_publication(value, **kwargs))["state"] == "failed"
