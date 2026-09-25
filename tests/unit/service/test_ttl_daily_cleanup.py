# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Daily admission, bounded backlog draining, and durable cleanup recovery."""

from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from openviking.core.ttl import hidden_by_ttl
from openviking.service import ttl_cleanup
from openviking.storage.ttl_registry import TTLRegistry
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from tests.unit.storage.test_ttl_registry import _MemoryAGFS, _record


class Clock(datetime):
    current = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz)


class CleanupQueue:
    def __init__(self):
        self.items = deque()
        self.peak = 0
        self.in_progress = 0

    async def size(self):
        return len(self.items)

    async def get_status(self):
        return SimpleNamespace(pending=len(self.items), in_progress=self.in_progress)

    async def enqueue(self, message):
        self.items.append(message)
        self.peak = max(self.peak, len(self.items))


def scheduler_for(registry, queue):
    service = SimpleNamespace(
        viking_fs=SimpleNamespace(ttl_registry=registry),
        _queue_manager=SimpleNamespace(TTL_CLEANUP="ttl_cleanup", get_queue=lambda name: queue),
    )
    return ttl_cleanup.TTLCleanupScheduler(service)


@pytest.fixture
def clock(monkeypatch):
    Clock.current = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(ttl_cleanup, "datetime", Clock)
    return Clock


@pytest.mark.asyncio
async def test_expired_object_waits_for_stable_jitter_window_across_restart(clock):
    agfs, queue = _MemoryAGFS(), CleanupQueue()
    registry = TTLRegistry(agfs)
    record = replace(_record(), expires_at="2026-09-23T11:00:00.000Z")
    await registry.upsert(record)
    cleanup_at = parse_iso_datetime(ttl_cleanup.cleanup_not_before(record))
    clock.current = cleanup_at - timedelta(microseconds=1)

    # Visibility has already expired, but physical deletion waits for its
    # stable per-object jitter window.
    assert hidden_by_ttl(record.expires_at, now=clock.current)
    await scheduler_for(registry, queue)._scan_once()
    assert not queue.items
    assert await registry.get(record.account_id, record.object_uri) == record

    # Ordinary registration and process restarts must retain the same deadline.
    registry = TTLRegistry(agfs)
    await registry.upsert(record)
    scheduler = scheduler_for(registry, queue)
    clock.current = cleanup_at - timedelta(microseconds=1)
    await scheduler._scan_once()
    assert not queue.items
    clock.current = cleanup_at
    await scheduler._scan_once()
    assert [item["target"]["object_uri"] for item in queue.items] == [record.object_uri]
    assert queue.items[0]["retry_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", ["2026-09-23T00:00:00.000Z", "2026-09-23T08:00:00+08:00"])
async def test_jitter_boundary_is_inclusive_and_normalizes_utc(clock, expires_at):
    registry, queue = TTLRegistry(_MemoryAGFS()), CleanupQueue()
    record = replace(_record(), expires_at=expires_at)
    await registry.upsert(record)
    cleanup_at = parse_iso_datetime(ttl_cleanup.cleanup_not_before(record))
    clock.current = cleanup_at - timedelta(microseconds=1)
    await scheduler_for(registry, queue)._scan_once()
    assert not queue.items
    clock.current = cleanup_at
    await scheduler_for(registry, queue)._scan_once()
    assert len(queue.items) == 1


@pytest.mark.asyncio
async def test_due_retry_keeps_identity_and_does_not_wait_another_day(clock):
    agfs, queue = _MemoryAGFS(), CleanupQueue()
    registry = TTLRegistry(agfs)
    record = replace(_record(), expires_at="2026-09-23T11:00:00.000Z")
    await registry.upsert(record)
    await registry.defer_retry(
        record,
        retry_count=2,
        task_id="original-task",
        next_retry_at=format_iso8601(clock.current + timedelta(seconds=40)),
    )
    scheduler = scheduler_for(TTLRegistry(agfs), queue)
    await scheduler._scan_once()
    assert not queue.items
    clock.current += timedelta(seconds=40)
    await scheduler._scan_once()
    assert queue.items[0]["task_id"] == "original-task"
    assert queue.items[0]["retry_count"] == 2


@pytest.mark.asyncio
async def test_daily_backlog_drains_multiple_pages_with_backpressure_and_restart(clock):
    agfs, queue = _MemoryAGFS(), CleanupQueue()
    registry = TTLRegistry(agfs)
    records = [
        replace(
            _record(uri=f"viking://user/u1/sessions/s{i:04}"),
            expires_at=format_iso8601(clock.current - timedelta(days=3, minutes=1005 - i)),
        )
        for i in range(1005)
    ]
    for record in reversed(records):
        await registry.upsert(record)
    future = replace(
        _record(uri="viking://user/u1/sessions/live"), expires_at="2999-01-01T00:00:00.000Z"
    )
    await registry.upsert(future)
    scheduler = scheduler_for(registry, queue)
    await scheduler._scan_once()
    assert len(queue.items) == 100

    # A stalled consumer must not accumulate new deliveries as claim leases age.
    reads = len(agfs.read_calls)
    clock.current += timedelta(minutes=10)
    for _ in range(3):
        await scheduler._scan_once()
    assert len(queue.items) == 100
    assert len(agfs.read_calls) == reads

    removed = []

    async def consume(count):
        for _ in range(count):
            item = queue.items.popleft()["target"]
            assert await registry.remove_if_generation(
                item["account_id"], item["object_uri"], item["generation"]
            )
            removed.append(item["object_uri"])

    # Even after partial consumption/restart, finish the durable page first.
    await consume(37)
    registry = TTLRegistry(agfs)
    scheduler = scheduler_for(registry, queue)
    await scheduler._scan_once()
    assert len(queue.items) == 63
    await consume(len(queue.items))
    for _ in range(20):
        await scheduler._scan_once()
        if not queue.items:
            break
        await consume(len(queue.items))
        clock.current += timedelta(seconds=30)

    expected = [
        record.object_uri
        for record in sorted(
            records, key=lambda item: ttl_cleanup.cleanup_not_before(item)
        )
    ]
    assert removed == expected
    assert queue.peak == 100
    assert await registry.get(future.account_id, future.object_uri) == future
    assert not queue.items


@pytest.mark.asyncio
async def test_enqueue_crash_is_recovered_after_claim_lease_without_next_day_wait(clock):
    agfs, queue = _MemoryAGFS(), CleanupQueue()
    registry = TTLRegistry(agfs)
    record = replace(_record(), expires_at="2020-01-01T00:00:00.000Z")
    await registry.upsert(record)

    async def crash(message):
        raise OSError("queue unavailable")

    queue.enqueue = crash
    with pytest.raises(OSError, match="queue unavailable"):
        await scheduler_for(registry, queue)._scan_once()
    recovered_queue = CleanupQueue()
    scheduler = scheduler_for(TTLRegistry(agfs), recovered_queue)
    await scheduler._scan_once()
    assert not recovered_queue.items
    clock.current += timedelta(minutes=6)
    await scheduler._scan_once()
    assert len(recovered_queue.items) == 1
