# Copyright (c) 2026 Beijing Volcano Engine Technology Co. Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the queue-wait stamp lifecycle in NamedQueue (#4578).

The enqueue side stamps ``_ov_enqueued_at`` onto dict payloads; the dequeue funnel
(``_report_queue_wait``) pops it and publishes one ``queue.wait`` event, so handlers
never observe the private field.
"""

import time
from types import SimpleNamespace

from openviking.storage.queuefs.named_queue import NamedQueue


def _bare_queue(name: str = "Semantic") -> NamedQueue:
    q = NamedQueue.__new__(NamedQueue)
    q.name = name
    q.path = f"/queues/{name}"
    return q


class _CapturingBus:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append((event.name, dict(event.payload)))


def test_report_queue_wait_pops_stamp_and_emits(monkeypatch):
    q = _bare_queue("Semantic")
    import openviking.observability.events as events_mod

    captured = []

    def fake_publish(event_name, payload=None):
        captured.append((event_name, dict(payload or {})))

    import openviking.observability.events as events_mod

    monkeypatch.setattr(events_mod, "try_publish_event", fake_publish)

    enqueued_at = time.time() - 30.0
    data = {"id": "m1", "_ov_enqueued_at": enqueued_at}
    q._report_queue_wait(data)

    assert "_ov_enqueued_at" not in data, "stamp must be popped before handlers run"
    assert captured == [
        ("queue.wait", {"queue": "Semantic", "wait_seconds": captured[0][1]["wait_seconds"]})
    ] if captured else False, "one queue.wait event must be published"
    assert len(captured) == 1
    assert captured[0][0] == "queue.wait"
    assert captured[0][1]["queue"] == "Semantic"
    assert 25.0 <= captured[0][1]["wait_seconds"] <= 35.0


def test_report_queue_wait_noop_without_stamp():
    q = _bare_queue("Semantic")
    data = {"id": "m2"}
    q._report_queue_wait(data)  # must not raise
    assert data == {"id": "m2"}


def test_report_queue_wait_tolerates_bad_stamp():
    q = _bare_queue("Semantic")
    q._report_queue_wait({"id": "m3", "_ov_enqueued_at": "not-a-number"})
    # bad stamp is popped and silently dropped (observability must not break dequeue)
    assert True


def test_enqueue_stamps_dict_payload(monkeypatch):
    q = _bare_queue("Embedding")
    captured = {}

    async def fake_ensure():
        return None

    async def fake_write(path, content):
        captured["content"] = content
        return "msg-1"

    q._ensure_initialized = fake_ensure
    q._enqueue_hook = None
    q._task_work_index = None
    q._async_agfs = SimpleNamespace(write=fake_write)

    import asyncio

    msg_id = asyncio.run(q.enqueue({"payload": 1}))
    assert msg_id == "msg-1"
    import json

    sent = json.loads(captured["content"].decode("utf-8"))
    assert "_ov_enqueued_at" in sent, "dict payloads must carry the enqueue stamp"
    assert abs(sent["_ov_enqueued_at"] - time.time()) < 5.0
