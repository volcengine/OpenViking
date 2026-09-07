# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Serial dequeue must keep in_progress accounting balanced.

The concurrent worker in QueueManager compensates when a handler raises
without calling report_error ("Handler did not call report_error; decrement
in_progress manually"). NamedQueue.dequeue() — used by the serial worker
path (e.g. the UserDeletion queue) — must do the same, otherwise a single
unreported handler failure leaks the in_progress counter and
is_complete()/wait_complete() never observe an idle queue again.
"""

import json
from typing import Any, Dict, List, Optional

from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue


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


class ExplodingHandler(DequeueHandlerBase):
    """Handler that raises without reporting (e.g. _UserDeletionProcessor
    when _process hits an await outside its stage try/excepts)."""

    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        raise RuntimeError("transient tracker storage failure")


class ReportingHandler(DequeueHandlerBase):
    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        self.report_error("handled failure")
        return None


class SuccessHandler(DequeueHandlerBase):
    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        self.report_success()
        return data


async def test_dequeue_handler_exception_does_not_leak_in_progress():
    agfs = FakeAGFS([{"id": "m1", "task_id": "t1"}])
    queue = NamedQueue(agfs, "/queue", "UserDeletion", dequeue_handler=ExplodingHandler())

    result = await queue.dequeue()

    assert result is None  # exception is still swallowed by dequeue()
    status = await queue.get_status()
    assert status.in_progress == 0, "in_progress must be balanced after an unreported failure"
    assert status.error_count == 1, "unreported failure must land in the error history"
    assert agfs.acked == [], "message must not be acked so RecoverStale can retry it"


async def test_dequeue_handler_exception_keeps_queue_completeobservable():
    """Two failed dequeues must leave is_all_complete() able to report idle."""
    agfs = FakeAGFS([{"id": "m1", "task_id": "t1"}, {"id": "m2", "task_id": "t2"}])
    queue = NamedQueue(agfs, "/queue", "UserDeletion", dequeue_handler=ExplodingHandler())

    await queue.dequeue()
    await queue.dequeue()

    status = await queue.get_status()
    assert status.pending == 0
    assert status.in_progress == 0
    assert status.is_complete is True


async def test_dequeue_reported_error_still_balances_and_acks():
    agfs = FakeAGFS([{"id": "m1", "task_id": "t1"}])
    queue = NamedQueue(agfs, "/queue", "UserDeletion", dequeue_handler=ReportingHandler())

    result = await queue.dequeue()

    assert result is None
    status = await queue.get_status()
    assert status.in_progress == 0
    assert status.error_count == 1
    assert agfs.acked == ["m1"], "handled errors are acked (terminal) as before"


async def test_dequeue_success_path_unchanged():
    agfs = FakeAGFS([{"id": "m1", "task_id": "t1"}])
    queue = NamedQueue(agfs, "/queue", "UserDeletion", dequeue_handler=SuccessHandler())

    result = await queue.dequeue()

    assert result == {"id": "m1", "task_id": "t1"}
    status = await queue.get_status()
    assert status.in_progress == 0
    assert status.processed == 1
    assert status.error_count == 0
    assert agfs.acked == ["m1"]
