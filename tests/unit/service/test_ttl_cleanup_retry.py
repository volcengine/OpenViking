"""Retry budgets and confirmation progress survive restart and duplicate delivery."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import AGFSHTTPError, AGFSNetworkError
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import (
    TaskStatus,
    TaskTracker,
    get_task_tracker,
    set_task_tracker,
)
from openviking.service.ttl_cleanup import _ttl_cleanup_message
from openviking.storage.errors import StorageException
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.ttl_registry import TTLRegistry
from openviking.utils.time_utils import parse_iso_datetime
from tests.unit.service.test_ttl_cleanup import EVENT_URI, _event_body, _make_service, _record
from tests.unit.service.test_ttl_cleanup import tracker as tracker
from tests.unit.service.test_ttl_daily_cleanup import CleanupQueue, scheduler_for
from tests.unit.service.test_ttl_daily_cleanup import clock as clock
from tests.unit.storage.test_ttl_registry import _MemoryAGFS


async def prepare(error):
    record = _record("event", object_uri=EVENT_URI)
    cleanup, fs, _, _ = _make_service(record=record, live_content=_event_body(), rm_error=error)
    storage = _MemoryAGFS()
    storage.mkdir = AsyncMock()
    fs.ttl_registry = TTLRegistry(storage)
    await fs.ttl_registry.upsert(record)
    # Both deliveries must use a real shared object lock, independently of the
    # task store's own keyed schedule lock.
    lock = asyncio.Lock()

    async def acquire(*args, **kwargs):
        await lock.acquire()
        return {"lease_ref": "object"}

    async def release(*args):
        lock.release()

    fs._async_agfs.pathlock_acquire_batch.side_effect = acquire
    fs._async_agfs.pathlock_release.side_effect = release
    return cleanup, fs, storage, record


def confirm_error(cause=None):
    exc = StorageException("deletion confirmation pending", action="confirm_delete")
    exc.__cause__ = cause or RuntimeError("vector residue")
    return exc


@pytest.mark.asyncio
async def test_retry_budget_and_backoff_survive_restart_and_duplicate_delivery(tracker, clock):
    cleanup, fs, storage, record = await prepare(AGFSNetworkError("unavailable"))
    first = _ttl_cleanup_message(record=record)
    for attempt, delay in enumerate((30, 60, 120, 86400, 86400)):
        # Recover both scheduling and task state, not merely the Python message.
        fs.ttl_registry = TTLRegistry(storage)
        set_task_tracker(TaskTracker(PersistentTaskStore(storage)))
        queue = CleanupQueue()
        await scheduler_for(fs.ttl_registry, queue)._scan_once()
        assert len(queue.items) == 1
        message = queue.items.popleft()
        assert message["retry_count"] == attempt
        assert (await cleanup._process(message)).outcome is ProcessOutcome.REQUEUED
        item = await fs.ttl_registry.get_scheduled(record.account_id, record.object_uri)
        wait = (parse_iso_datetime(item["run_at"]) - clock.current).total_seconds()
        assert delay <= wait <= delay * 1.2
        assert item["payload"]["retry_count"] == attempt + 1

        # An old QueueFS delivery must neither invoke delete nor shorten backoff.
        assert (await cleanup._process(first)).outcome is ProcessOutcome.REQUEUED
        assert fs.rm.await_count == attempt + 1
        assert await fs.ttl_registry.get_scheduled(record.account_id, record.object_uri) == item
        task = await get_task_tracker().get(
            message["task_id"], account_id=message["account_id"], user_id=message["user_id"]
        )
        assert task.status is TaskStatus.RUNNING
        assert task.stage == ("retry_cooldown" if attempt >= 3 else "retrying")
        assert await fs.ttl_registry.get(record.account_id, record.object_uri) == record
        clock.current = parse_iso_datetime(item["run_at"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,cooldown",
    [
        (AGFSHTTPError("denied", status_code=403), True),
        (ValueError("bad metadata"), True),
        (StorageException("bad request", action="Delete", retryable=False), True),
        (AGFSHTTPError("busy", status_code=429), False),
        (AGFSNetworkError("endpoint not found, 403 in diagnostic"), False),
        (confirm_error(AGFSHTTPError("denied", status_code=401)), True),
    ],
)
async def test_retry_classification_uses_structured_errors(tracker, clock, error, cooldown):
    cleanup, fs, _, record = await prepare(error)
    await cleanup._process(_ttl_cleanup_message(record=record))
    item = await fs.ttl_registry.get_scheduled(record.account_id, record.object_uri)
    delay = (parse_iso_datetime(item["run_at"]) - clock.current).total_seconds()
    assert (delay >= 86400) == cooldown
    assert await fs.ttl_registry.get(record.account_id, record.object_uri) == record


@pytest.mark.asyncio
async def test_concurrent_duplicate_failure_schedules_one_retry(tracker):
    cleanup, fs, _, record = await prepare(AGFSNetworkError("outage"))
    message = _ttl_cleanup_message(record=record)
    await asyncio.gather(cleanup._process(message), cleanup._process(message))
    assert fs.rm.await_count == 1
    item = await fs.ttl_registry.get_scheduled(record.account_id, record.object_uri)
    assert item["payload"]["retry_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("residue", [False, True])
async def test_confirmation_retry_then_repair_real_residue(tracker, clock, residue):
    cleanup, fs, storage, record = await prepare(confirm_error())
    await cleanup._process(_ttl_cleanup_message(record=record))
    for expected_verify in [True, False] if residue else [True]:
        fs.ttl_registry = TTLRegistry(storage)
        item = await fs.ttl_registry.get_scheduled(record.account_id, record.object_uri)
        assert item["payload"]["verify_only"] == expected_verify
        clock.current = parse_iso_datetime(item["run_at"])
        queue = CleanupQueue()
        await scheduler_for(fs.ttl_registry, queue)._scan_once()
        fs.rm.side_effect = confirm_error() if residue and expected_verify else None
        await cleanup._process(queue.items.popleft())
        assert fs.rm.await_args.kwargs.get("verify_only", False) == expected_verify
    assert await fs.ttl_registry.get(record.account_id, record.object_uri) is None


@pytest.mark.asyncio
async def test_old_deadline_delivery_cannot_consume_new_expiry_budget(tracker):
    cleanup, fs, _, record = await prepare(None)
    renewed = replace(record, expires_at="2999-01-01T00:00:00.000Z")
    await fs.ttl_registry.upsert(renewed)
    assert (
        await cleanup._process(_ttl_cleanup_message(record=record))
    ).outcome is ProcessOutcome.SUCCESS
    fs.rm.assert_not_awaited()
    assert await fs.ttl_registry.get(record.account_id, record.object_uri) == renewed


@pytest.mark.asyncio
async def test_inflight_cleanup_prevents_reclaiming_expired_lease(clock):
    registry, queue = TTLRegistry(_MemoryAGFS()), CleanupQueue()
    await registry.upsert(_record("event", object_uri=EVENT_URI))
    scheduler = scheduler_for(registry, queue)
    await scheduler._scan_once()
    queue.items.popleft()
    queue.in_progress = 1
    clock.current += timedelta(minutes=10)
    await scheduler._scan_once()
    assert not queue.items
