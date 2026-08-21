# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for startup recovery of stale ``processing`` rows (#3895).

When a worker crashes between dequeue and ack, the message stays in
``processing`` forever. These tests cover the Python-layer recovery
(``NamedQueue.recover_stale`` / ``QueueManager.recover_stale_all``) that resets
stale rows back to ``pending`` on startup, and the new ``recover_stale_sec``
default.

The real QueueFS backend lives in the RAGFS binding (ragfs_python) and is not
available in this test environment, so we drive ``NamedQueue`` through an
in-memory fake that speaks the same ``/enqueue`` ``/dequeue`` ``/ack``
``/messages`` ``/size`` path verbs.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from openviking.storage.queuefs.named_queue import (
    NamedQueue,
    _parse_processing_started_at,
)
from openviking.storage.queuefs.queue_manager import QueueManager


class FakeQueueBackend:
    """Minimal in-memory QueueFS backend keyed by message id.

    Implements the sync ``read``/``write``/``mkdir`` verbs the AGFS adapter
    invokes (always with a ``ctx`` kwarg). Messages carry an explicit
    ``status`` and ``processing_started_at`` so recovery can find stale rows.
    """

    def __init__(self) -> None:
        self._msgs: Dict[str, Dict[str, Any]] = {}
        self._counter = 0
        self._lock = threading.Lock()

    # -- helpers ---------------------------------------------------------
    def _new_id(self) -> str:
        self._counter += 1
        return f"msg-{self._counter}"

    def seed(self, msg: Dict[str, Any]) -> str:
        """Add a pre-built message (e.g. a stale processing row) and return its id."""
        with self._lock:
            assert "id" in msg, "seeded message must include an id"
            self._msgs[msg["id"]] = dict(msg)
            return msg["id"]

    # -- path verbs ------------------------------------------------------
    def mkdir(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def read(self, path: str, **kwargs: Any) -> bytes:
        if path.endswith("/messages"):
            with self._lock:
                return json.dumps(list(self._msgs.values())).encode("utf-8")
        if path.endswith("/size"):
            with self._lock:
                pending = sum(1 for m in self._msgs.values() if m.get("status") == "pending")
            return str(pending).encode("utf-8")
        if path.endswith("/dequeue"):
            with self._lock:
                for msg in self._msgs.values():
                    if msg.get("status") == "pending":
                        msg["status"] = "processing"
                        msg["processing_started_at"] = datetime.now(timezone.utc).isoformat()
                        return json.dumps(msg).encode("utf-8")
            return b"{}"
        if path.endswith("/peek"):
            with self._lock:
                for msg in self._msgs.values():
                    if msg.get("status") == "pending":
                        return json.dumps(msg).encode("utf-8")
            return b"{}"
        return b""

    def write(self, path: str, data: bytes, **kwargs: Any) -> str:
        if path.endswith("/enqueue"):
            payload = json.loads(data.decode("utf-8")) if isinstance(data, (bytes, bytearray)) else json.loads(data)
            with self._lock:
                msg_id = self._new_id()
                msg: Dict[str, Any] = {"id": msg_id, "status": "pending"}
                msg.update(payload)
                # Enqueued payloads must never inherit a stale processing state.
                msg.pop("processing_started_at", None)
                msg["status"] = "pending"
                self._msgs[msg_id] = msg
            return msg_id
        if path.endswith("/ack"):
            msg_id = (data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)).strip()
            with self._lock:
                self._msgs.pop(msg_id, None)
            return msg_id
        if path.endswith("/clear"):
            with self._lock:
                self._msgs.clear()
            return ""
        return ""

    # -- inspection ------------------------------------------------------
    def all_messages(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(m) for m in self._msgs.values()]


def _stale_processing_row(
    *, msg_id: str, payload: Dict[str, Any], age_sec: float
) -> Dict[str, Any]:
    started = datetime.now(timezone.utc) - timedelta(seconds=age_sec)
    return {
        "id": msg_id,
        "status": "processing",
        "processing_started_at": started.isoformat(),
        **payload,
    }


# --------------------------------------------------------------------------
# Timestamp parser
# --------------------------------------------------------------------------


def test_parse_processing_started_at_handles_iso_epoch_and_none() -> None:
    iso = datetime.now(timezone.utc).isoformat()
    assert _parse_processing_started_at(iso) is not None
    assert _parse_processing_started_at("1700000000") == 1700000000.0
    assert _parse_processing_started_at(1700000000) == 1700000000.0
    assert _parse_processing_started_at(None) is None
    assert _parse_processing_started_at("not-a-time") is None
    assert _parse_processing_started_at("") is None


# --------------------------------------------------------------------------
# NamedQueue.recover_stale
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_stale_resets_old_processing_row_to_pending() -> None:
    backend = FakeQueueBackend()
    backend.seed(
        _stale_processing_row(msg_id="stale-1", payload={"task": "embed-A"}, age_sec=600)
    )
    queue = NamedQueue(backend, "/queue", "TestQueue")

    reset = await queue.recover_stale(300)

    assert reset == 1
    msgs = backend.all_messages()
    assert len(msgs) == 1
    assert msgs[0]["status"] == "pending"
    assert msgs[0]["task"] == "embed-A"
    assert "stale-1" not in {m["id"] for m in msgs}  # old row deleted, new id assigned


@pytest.mark.asyncio
async def test_recover_stale_then_dequeue_replays_payload() -> None:
    backend = FakeQueueBackend()
    backend.seed(
        _stale_processing_row(msg_id="stale-1", payload={"task": "embed-A"}, age_sec=600)
    )
    queue = NamedQueue(backend, "/queue", "TestQueue")

    assert await queue.recover_stale(300) == 1

    # No dequeue handler => dequeue() reads, then acks immediately.
    data = await queue.dequeue()
    assert data is not None
    assert data["task"] == "embed-A"


@pytest.mark.asyncio
async def test_recover_stale_leaves_fresh_processing_row_alone() -> None:
    backend = FakeQueueBackend()
    # Only 5s old, threshold is 300s -> still active, must not be reset.
    backend.seed(_stale_processing_row(msg_id="fresh-1", payload={"task": "x"}, age_sec=5))
    queue = NamedQueue(backend, "/queue", "TestQueue")

    assert await queue.recover_stale(300) == 0

    msgs = backend.all_messages()
    assert len(msgs) == 1
    assert msgs[0]["status"] == "processing"
    assert msgs[0]["id"] == "fresh-1"


@pytest.mark.asyncio
async def test_recover_stale_leaves_pending_rows_alone() -> None:
    backend = FakeQueueBackend()
    backend.seed({"id": "pend-1", "status": "pending", "task": "x"})
    queue = NamedQueue(backend, "/queue", "TestQueue")

    assert await queue.recover_stale(300) == 0
    assert backend.all_messages() == [
        {"id": "pend-1", "status": "pending", "task": "x"}
    ]


@pytest.mark.asyncio
async def test_recover_stale_disabled_when_threshold_zero() -> None:
    backend = FakeQueueBackend()
    backend.seed(_stale_processing_row(msg_id="stale-1", payload={"task": "x"}, age_sec=9999))
    queue = NamedQueue(backend, "/queue", "TestQueue")

    assert await queue.recover_stale(0) == 0
    msgs = backend.all_messages()
    assert len(msgs) == 1
    assert msgs[0]["status"] == "processing"


@pytest.mark.asyncio
async def test_recover_stale_is_idempotent() -> None:
    backend = FakeQueueBackend()
    backend.seed(_stale_processing_row(msg_id="stale-1", payload={"task": "x"}, age_sec=600))
    queue = NamedQueue(backend, "/queue", "TestQueue")

    assert await queue.recover_stale(300) == 1
    # Second sweep finds only the freshly-enqueued pending row -> resets nothing.
    assert await queue.recover_stale(300) == 0
    assert len(backend.all_messages()) == 1


@pytest.mark.asyncio
async def test_recover_stale_accepts_epoch_timestamp() -> None:
    backend = FakeQueueBackend()
    old_epoch = time.time() - 600
    backend.seed(
        {
            "id": "stale-epoch",
            "status": "processing",
            "processing_started_at": old_epoch,
            "task": "x",
        }
    )
    queue = NamedQueue(backend, "/queue", "TestQueue")

    assert await queue.recover_stale(300) == 1


# --------------------------------------------------------------------------
# QueueManager wiring
# --------------------------------------------------------------------------


def test_queue_manager_default_recover_stale_sec_is_300() -> None:
    manager = QueueManager(agfs=object())
    assert manager._recover_stale_sec == 300


@pytest.mark.asyncio
async def test_queue_manager_recover_stale_all_sweeps_each_queue() -> None:
    backend_a = FakeQueueBackend()
    backend_a.seed(_stale_processing_row(msg_id="s-a", payload={"t": 1}, age_sec=600))
    backend_b = FakeQueueBackend()
    backend_b.seed(_stale_processing_row(msg_id="s-b", payload={"t": 2}, age_sec=600))

    manager = QueueManager(agfs=object(), recover_stale_sec=300)
    # Bypass get_queue() (which would share one agfs); register queues directly.
    manager._queues["A"] = NamedQueue(backend_a, "/queue", "A")
    manager._queues["B"] = NamedQueue(backend_b, "/queue", "B")

    total = await manager.recover_stale_all()

    assert total == 2
    assert backend_a.all_messages()[0]["status"] == "pending"
    assert backend_b.all_messages()[0]["status"] == "pending"


def test_queue_manager_start_runs_recovery_before_workers() -> None:
    """start() must reset stale rows before consumer workers begin."""
    backend = FakeQueueBackend()
    backend.seed(_stale_processing_row(msg_id="s-1", payload={"task": "z"}, age_sec=600))

    manager = QueueManager(agfs=object(), recover_stale_sec=300)
    # No dequeue handler => the worker loop polls without dequeuing, so it will
    # not race the recovery assertions.
    manager._queues["TestQueue"] = NamedQueue(backend, "/queue", "TestQueue")

    try:
        manager.start()
        msgs = backend.all_messages()
        assert len(msgs) == 1
        assert msgs[0]["status"] == "pending"
        assert msgs[0]["task"] == "z"
    finally:
        manager.stop()


@pytest.mark.asyncio
async def test_prepare_task_tracking_recovers_before_rebuild() -> None:
    """Recovery runs before the TaskWorkIndex snapshot is rebuilt."""
    backend = FakeQueueBackend()
    backend.seed(_stale_processing_row(msg_id="s-1", payload={"task": "z"}, age_sec=600))

    manager = QueueManager(agfs=object(), recover_stale_sec=300)
    manager._queues["TestQueue"] = NamedQueue(backend, "/queue", "TestQueue")

    class _FakeTracker:
        def attach_work_index(self, index: Any) -> None:
            return None

        async def restore_work_tasks(self, owners: Any) -> None:
            return None

    # Spy on TaskWorkIndex.rebuild to capture the statuses seen by the rebuild,
    # which proves recovery already ran (no row left in 'processing').
    rebuilt_states: List[str] = []
    original_rebuild = manager._task_work_index.rebuild

    def _spy(snapshots):
        for _name, snap in snapshots.items():
            for msg in snap:
                rebuilt_states.append(msg.get("status"))
        return original_rebuild(snapshots)

    manager._task_work_index.rebuild = _spy  # type: ignore[method-assign]

    await manager.prepare_task_tracking(_FakeTracker())

    # The rebuild saw only a pending row (recovery ran first); no 'processing'.
    assert rebuilt_states == ["pending"]
