# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from typing import Sequence

import pytest

from openviking.observability.events import ObservabilityEvent
from openviking.observability.usage_audit.worker import UsageAuditWorker


class FakeStore:
    def __init__(self) -> None:
        self.batches: list[list[ObservabilityEvent]] = []

    async def record_batch(self, events: Sequence[ObservabilityEvent]) -> None:
        self.batches.append(list(events))


class SlowFirstFlushStore:
    def __init__(self) -> None:
        self.batches: list[list[ObservabilityEvent]] = []
        self.calls = 0
        self.started = asyncio.Event()

    async def record_batch(self, events: Sequence[ObservabilityEvent]) -> None:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await asyncio.sleep(0.05)
        self.batches.append(list(events))


@pytest.mark.asyncio
async def test_usage_audit_worker_close_flushes_queued_events():
    store = FakeStore()
    worker = UsageAuditWorker(
        store,
        queue_size=10,
        batch_size=10,
        flush_interval_seconds=1.0,
    )
    await worker.start()
    worker.enqueue(ObservabilityEvent(event_name="demo", payload={"value": 1}))

    await worker.close(timeout_seconds=0.2)

    assert [[event.event_name for event in batch] for batch in store.batches] == [["demo"]]


@pytest.mark.asyncio
async def test_usage_audit_worker_close_waits_for_inflight_batch_after_timeout():
    store = SlowFirstFlushStore()
    worker = UsageAuditWorker(
        store,
        queue_size=10,
        batch_size=10,
        flush_interval_seconds=1.0,
    )
    await worker.start()
    worker.enqueue(ObservabilityEvent(event_name="demo", payload={"value": 1}))
    await asyncio.wait_for(store.started.wait(), timeout=1.0)

    await worker.close(timeout_seconds=0.01)

    assert store.calls == 1
    assert [[event.event_name for event in batch] for batch in store.batches] == [["demo"]]
