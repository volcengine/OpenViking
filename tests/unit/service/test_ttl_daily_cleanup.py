# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Paced directory discovery with queue backpressure and a daily pass."""

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from openviking.service import ttl_cleanup
from openviking_cli.utils.config.ttl_config import TTLCleanupConfig
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import root_ctx


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

    async def get_status(self):
        return SimpleNamespace(pending=len(self.items), in_progress=self.in_progress)

    async def enqueue(self, message):
        self.items.append(message)
        self.peak = max(self.peak, len(self.items))


def scheduler_for(fs, queue):
    service = SimpleNamespace(
        viking_fs=fs,
        _queue_manager=SimpleNamespace(TTL_CLEANUP="ttl_cleanup", get_queue=lambda name: queue),
    )
    return ttl_cleanup.TTLCleanupScheduler(service)


@pytest.fixture
def clock(monkeypatch):
    Clock.current = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(ttl_cleanup, "datetime", Clock)
    return Clock


@pytest.mark.asyncio
async def test_scan_finishes_pages_and_retries_only_after_daily_pass(
    binding_fs, monkeypatch, clock
):
    fs, ctx, queue = binding_fs, root_ctx(), CleanupQueue()
    config = TTLCleanupConfig(batch_size=2)
    monkeypatch.setattr(ttl_cleanup, "_cleanup_settings", lambda: config)
    owners = [f"viking://user/default/sessions/s{i}" for i in range(5)]
    for uri in owners:
        await fs.write_file(
            uri + "/.meta.json", json.dumps({"expires_at": "2000-01-01T00:00:00Z"}), ctx=ctx
        )
    scheduler = scheduler_for(fs, queue)
    await scheduler._scan_once()
    assert len(queue.items) == 2
    first = list(queue.items)
    await scheduler._scan_once()
    assert list(queue.items) == first
    queue.items.clear()
    queue.in_progress = 1
    await scheduler._scan_once()
    assert not queue.items
    queue.in_progress = 0
    seen = [item["target"]["object_uri"] for item in first]
    for _ in range(3):
        await scheduler._scan_once()
        seen.extend(item["target"]["object_uri"] for item in queue.items)
        queue.items.clear()
    assert seen == owners
    assert queue.peak == 2
    assert scheduler._candidates is None
    await scheduler._scan_once()
    assert not queue.items
    clock.current += timedelta(days=1)
    await scheduler._scan_once()
    assert len(queue.items) == 2


@pytest.mark.asyncio
async def test_failed_enqueue_and_pause_leave_metadata_for_restart(binding_fs, monkeypatch, clock):
    fs, ctx, queue = binding_fs, root_ctx(), CleanupQueue()
    owner = "viking://user/default/sessions/recover"
    await fs.write_file(owner + "/.meta.json", '{"expires_at":"2000-01-01T00:00:00Z"}', ctx=ctx)
    config = TTLCleanupConfig(enabled=False)
    monkeypatch.setattr(ttl_cleanup, "_cleanup_settings", lambda: config)
    scheduler = scheduler_for(fs, queue)
    await scheduler._scan_once()
    assert not queue.items
    config.enabled = True

    async def fail(message):
        raise OSError("queue unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(queue, "enqueue", fail)
        await scheduler._scan_once()
    # No claim lease or task history is needed to recover a lost enqueue.
    await scheduler_for(fs, queue)._scan_once()
    assert [item["target"]["object_uri"] for item in queue.items] == [owner]
