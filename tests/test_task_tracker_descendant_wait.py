# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""wait_for_descendants must be bounded so orphaned descendant work cannot hang
the parent AddResource task in ``processing`` forever (issue #4341)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from openviking.service.task_tracker import TaskTracker
from openviking.service.task_work_index import QueueTaskMetadata


class _StubStore:
    """TaskTracker only touches the store's class name during __init__."""

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"stub store has no {name}")


def _tracker(descendant_wait_timeout_s: float | None) -> TaskTracker:
    return TaskTracker(
        _StubStore(),  # type: ignore[arg-type]
        descendant_wait_timeout_s=descendant_wait_timeout_s,
    )


def _meta(task_id: str, work_id: str) -> QueueTaskMetadata:
    return QueueTaskMetadata(task_id=task_id, work_id=work_id)


@pytest.mark.asyncio
async def test_wait_returns_immediately_when_no_other_work() -> None:
    tracker = _tracker(descendant_wait_timeout_s=5.0)
    tracker._work_index.register("AddResource", _meta("t1", "w-self"))
    start = time.monotonic()
    await tracker.wait_for_descendants("t1", current_work_id="w-self")
    assert time.monotonic() - start < 1.0


@pytest.mark.asyncio
async def test_wait_returns_when_descendant_reaches_terminal_ack() -> None:
    tracker = _tracker(descendant_wait_timeout_s=5.0)
    index = tracker._work_index
    index.register("AddResource", _meta("t2", "w-self"))
    index.register("Semantic", _meta("t2", "w-child"))

    async def ack_child_after(delay: float) -> None:
        await asyncio.sleep(delay)
        await index.prepare_ack("Semantic", _meta("t2", "w-child"))

    acker = asyncio.create_task(ack_child_after(0.2))
    start = time.monotonic()
    await tracker.wait_for_descendants("t2", current_work_id="w-self")
    elapsed = time.monotonic() - start
    assert 0.1 < elapsed < 4.0, "should return shortly after the descendant ACKs"
    await acker


@pytest.mark.asyncio
async def test_orphaned_descendant_times_out_and_parent_proceeds() -> None:
    tracker = _tracker(descendant_wait_timeout_s=0.15)
    index = tracker._work_index
    index.register("AddResource", _meta("t3", "w-self"))
    # Simulate an orphan: registered but its finalization callback never fires.
    index.register("Embedding", _meta("t3", "w-orphan"))

    start = time.monotonic()
    await tracker.wait_for_descendants("t3", current_work_id="w-self")
    elapsed = time.monotonic() - start
    assert 0.1 < elapsed < 3.0, "must return after the bounded timeout, not hang"


@pytest.mark.asyncio
async def test_timeout_logs_orphaned_work_ids() -> None:
    tracker = _tracker(descendant_wait_timeout_s=0.05)
    index = tracker._work_index
    index.register("AddResource", _meta("t4", "w-self"))
    index.register("Semantic", _meta("t4", "w-orphan"))

    await tracker.wait_for_descendants("t4", current_work_id="w-self")

    assert index.work_ids("t4", exclude_work_id="w-self") == (
        ("Semantic", "w-orphan"),
    )


@pytest.mark.asyncio
async def test_unbounded_legacy_mode_still_waits_until_ack() -> None:
    tracker = _tracker(descendant_wait_timeout_s=None)
    index = tracker._work_index
    index.register("AddResource", _meta("t5", "w-self"))
    index.register("Semantic", _meta("t5", "w-child"))

    async def ack_child_after() -> None:
        await asyncio.sleep(0.2)
        await index.prepare_ack("Semantic", _meta("t5", "w-child"))

    acker = asyncio.create_task(ack_child_after())
    await tracker.wait_for_descendants("t5", current_work_id="w-self")
    await acker
    assert not index.has_work("t5", exclude_work_id="w-self")
