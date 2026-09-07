# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Completion-accounting correctness for queue waits and the shutdown drain.

Covers two invariants of the queuefs completion signal:

1. ``QueueManager.wait_complete`` must measure its deadline with a monotonic
   clock: a wall-clock jump (NTP correction, VM snapshot restore) must not
   turn an in-flight wait into a false ``TimeoutError`` nor make the
   deadline unreachable.
2. When the concurrent worker's shutdown drain cancels an in-flight task,
   the ``in_progress`` slot claimed at dispatch must be released (without
   recording a processing error) so ``is_complete()``/``wait_complete()``
   can still observe an idle queue. The message itself stays un-acked, so
   at-least-once delivery via RecoverStale is preserved.
"""

import asyncio
import json
import threading
import time
from typing import Any, Dict, List, Optional

import pytest

from openviking.storage.queuefs import queue_manager as queue_manager_module
from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
from openviking.storage.queuefs.queue_manager import QueueManager


class FakeAGFS:
    """Minimal sync AGFS client fake for the queue control files."""

    def __init__(self, messages: List[Dict[str, Any]]):
        self.messages = list(messages)
        self.acked: List[str] = []

    def mkdir(self, path: str, ctx: Optional[Dict[str, str]] = None, **kw) -> Dict[str, Any]:
        return {}

    def read(self, path: str, ctx: Optional[Dict[str, str]] = None, **kw) -> Any:
        if path.endswith("/dequeue"):
            if self.messages:
                return json.dumps(self.messages.pop(0)).encode("utf-8")
            return b""
        if path.endswith("/size"):
            return str(len(self.messages)).encode("utf-8")
        return b""

    def write(self, path: str, data: bytes, ctx: Optional[Dict[str, str]] = None, **kw) -> str:
        if path.endswith("/ack"):
            self.acked.append(data.decode("utf-8"))
        return "written"


class FakeClock:
    """Stand-in for the ``time`` module binding inside queue_manager.

    Each read advances the corresponding clock by ``time_step`` /
    ``monotonic_step``, simulating a wall-clock jump while the monotonic
    clock keeps its normal cadence.
    """

    def __init__(self, *, time_step: float = 0.0, monotonic_step: float = 0.0):
        self._t = 1_000_000.0
        self._m = 500.0
        self._time_step = time_step
        self._monotonic_step = monotonic_step

    def time(self) -> float:
        self._t += self._time_step
        return self._t

    def monotonic(self) -> float:
        self._m += self._monotonic_step
        return self._m


class BlockingHandler(DequeueHandlerBase):
    """Handler that never finishes on its own; only cancellation unblocks it."""

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        await asyncio.Event().wait()
        return data


async def test_wait_complete_ignores_wall_clock_jumps(monkeypatch) -> None:
    manager = QueueManager(agfs=FakeAGFS([]))
    queue = manager.get_queue("TestQ", allow_create=True)
    queue._on_dequeue_start()  # queue is busy; finished shortly after

    async def _finish_later() -> None:
        await asyncio.sleep(0.15)
        queue._on_process_success()

    finisher = asyncio.create_task(_finish_later())
    # Wall clock jumps forward by 5000s on every read; monotonic stays sane.
    monkeypatch.setattr(
        queue_manager_module, "time", FakeClock(time_step=5000.0)
    )

    statuses = await manager.wait_complete("TestQ", timeout=30.0, poll_interval=0.02)

    await finisher
    assert statuses["TestQ"].is_complete
    assert statuses["TestQ"].in_progress == 0


async def test_wait_complete_still_times_out_on_a_stuck_queue() -> None:
    manager = QueueManager(agfs=FakeAGFS([]))
    queue = manager.get_queue("TestQ", allow_create=True)
    queue._on_dequeue_start()  # stuck busy forever

    with pytest.raises(TimeoutError):
        await manager.wait_complete("TestQ", timeout=0.3, poll_interval=0.05)

    queue._on_process_success()  # rebalance the counter for teardown


async def test_drain_cancel_releases_in_progress_without_error() -> None:
    agfs = FakeAGFS([{"id": "m1", "task_id": "t1"}])
    queue = NamedQueue(agfs, "/queue", "Embedding", dequeue_handler=BlockingHandler())
    manager = QueueManager(agfs=agfs)
    stop = threading.Event()

    worker = asyncio.create_task(
        manager._worker_async_concurrent(queue, stop, max_concurrent=1, drain_timeout=0.1)
    )

    # Wait until the message has been dispatched (in_progress claimed).
    deadline = time.monotonic() + 5.0
    while queue._in_progress < 1 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert queue._in_progress == 1, "message must be dispatched to the handler"

    stop.set()
    await asyncio.wait_for(worker, timeout=10.0)

    status = await queue.get_status()
    assert status.in_progress == 0, "cancelled drain must release the in_progress slot"
    assert status.error_count == 0, "a cancelled drain is not a processing failure"
    assert agfs.acked == [], "message must stay un-acked so RecoverStale can retry it"
