"""The official queue event retains only store-validated delivery ownership."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from openviking.observability.events import get_event_bus
from openviking.observability.performance import configure_performance
from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore
from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.service.task_work_index import bind_task_context, get_task_context
from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
from openviking.storage.queuefs.process_result import ProcessResult
from tests.test_task_tracker import _FakeAgfs


@pytest.fixture
async def delivery(monkeypatch):
    configure_performance(True)
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    await tracker.create("content_write", account_id="a", user_id="alice", task_id="task")
    await tracker.register_work("task", "work", "ContentWrite", account_id="a", user_id="alice")
    events = []
    bus = get_event_bus()
    bus.register("queue-observation-test", lambda event: events.append(event))
    message = {
        "id": "delivery",
        "timestamp": 1000.0,
        "data": json.dumps(
            {
                "task_id": "task",
                "_task_work_id": "work",
                "_task_account_id": "a",
                "_task_user_id": "alice",
                "body": "synthetic private body not copied",
            }
        ),
    }
    clocks = iter((10.0, 12.0))
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.time",
        SimpleNamespace(perf_counter=lambda: next(clocks), time=lambda: 1007.0),
    )
    try:
        yield tracker, message, events
    finally:
        bus.unregister("queue-observation-test")
        configure_performance(False)
        set_task_tracker(None)


def queue_events(events):
    return [event for event in events if event.event_name == "queue.processed"]


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled", "raise", "interrupt"])
async def test_verified_work_reaches_scoped_ledger_after_middleware_unwinds(
    delivery, tmp_path, outcome
):
    _, message, captured = delivery

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            context = get_task_context()
            assert (context.account_id, context.user_id, context.task_id, context.work_id) == (
                "a",
                "alice",
                "task",
                "work",
            )
            if outcome == "raise":
                raise RuntimeError("synthetic private exception not copied")
            if outcome == "interrupt":
                raise asyncio.CancelledError
            if outcome == "failed":
                return ProcessResult.failed("synthetic private failure not copied")
            return ProcessResult.cancelled() if outcome == "cancelled" else ProcessResult.success()

    queue = NamedQueue(
        object(),
        "/queue",
        "ContentWrite",
        dequeue_handler=Handler(),
        middlewares=[TaskWorkQueueMiddleware()],
    )
    # An unrelated inherited request/Task cannot become this delivery's owner.
    with bind_task_context("wrong", "b", "bob", work_id="wrong-work"):
        if outcome in {"raise", "interrupt"}:
            with pytest.raises(RuntimeError if outcome == "raise" else asyncio.CancelledError):
                await queue.process_dequeued(message)
        else:
            await queue.process_dequeued(message)
    event = queue_events(captured)[0]
    assert (
        event.account_id,
        event.user_id,
        event.payload["task_id"],
        event.payload["work_id"],
    ) == ("a", "alice", "task", "work")
    assert event.payload["stage"] == "queue.ContentWrite"
    assert event.payload["process_duration_seconds"] == 2.0
    assert event.payload["queue_seconds"] == 5.0
    assert event.payload["status"] == {"raise": "failed", "interrupt": "cancelled"}.get(
        outcome, outcome
    )
    assert "synthetic private" not in str(event.payload)
    store = SQLiteUsageAuditStore(tmp_path / "observations.db", performance_enabled=True)
    await store.initialize()
    try:
        await store.record_batch([event])
        now = datetime.now(timezone.utc)
        query = {
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "view": "details",
        }
        result = await store.query_performance(account_id="a", user_id="alice", **query)
        assert result["items"][0]["work_id"] == "work"
        assert result["items"][0]["queue_seconds"] == 5.0
        assert (
            result["summary"]["duration_by_kind"]["queue.processed"]["observed_span_seconds"] == 2.0
        )
        if outcome in {"failed", "raise"}:
            assert result["summary"]["failed_observations"] == 1
        elif outcome in {"cancelled", "interrupt"}:
            assert result["summary"]["cancelled_observations"] == 1
        other = await store.query_performance(account_id="b", user_id="bob", **query)
        assert other["summary"]["observations"] == 0
    finally:
        await store.close()


@pytest.mark.parametrize("kind", ["legacy", "wrong-owner", "shared"])
async def test_unverified_or_shared_delivery_never_uses_inherited_first_task(delivery, kind):
    _, message, captured = delivery
    payload = json.loads(message["data"])
    if kind == "legacy":
        payload = {"task_id": "body-only", "text": "private"}
    elif kind == "wrong-owner":
        payload["_task_account_id"] = "other"
    else:
        payload["source_task_ids"] = ["task", "another"]
    message["data"] = json.dumps(payload)

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            return ProcessResult.success()

    queue = NamedQueue(
        object(),
        "/queue",
        "ContentWrite",
        dequeue_handler=Handler(),
        middlewares=[TaskWorkQueueMiddleware()] if kind != "legacy" else [],
    )
    with bind_task_context("first-task", "private-account", "private-user", work_id="first-work"):
        await queue.process_dequeued(message)
    event = queue_events(captured)[0]
    assert not event.account_id and not event.user_id
    assert not event.payload["task_id"] and not event.payload["work_id"]
    assert "private-account" not in str(event.payload)
    assert "private-user" not in str(event.payload)


@pytest.mark.parametrize("timestamp", [None, 1008.0, 1006.0])
async def test_missing_or_incomparable_clock_does_not_invent_queue_wait(delivery, timestamp):
    _, message, captured = delivery
    message["timestamp"] = timestamp

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            return ProcessResult.success()

    queue = NamedQueue(
        object(),
        "/queue",
        "ContentWrite",
        dequeue_handler=Handler(),
        middlewares=[TaskWorkQueueMiddleware()],
    )
    await queue.process_dequeued(message)
    event = queue_events(captured)[0]
    assert event.account_id == "a" and "queue_seconds" not in event.payload


async def test_cancel_before_admission_has_a_verified_task_but_no_invented_work(delivery):
    tracker, message, captured = delivery
    await tracker.create("compile", account_id="a", user_id="alice", task_id="cancelled-task")
    await tracker.register_work(
        "cancelled-task", "work", "ContentWrite", account_id="a", user_id="alice"
    )
    payload = json.loads(message["data"])
    payload["task_id"] = "cancelled-task"
    message["data"] = json.dumps(payload)
    await tracker.cancel("cancelled-task", account_id="a", user_id="alice")

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            raise AssertionError("Cancelled Work must not run")

    queue = NamedQueue(
        object(),
        "/queue",
        "ContentWrite",
        dequeue_handler=Handler(),
        middlewares=[TaskWorkQueueMiddleware()],
    )
    result = await queue.process_dequeued(message)
    assert result.outcome.value == "cancelled"
    event = queue_events(captured)[0]
    assert (event.account_id, event.user_id, event.payload["task_id"]) == (
        "a",
        "alice",
        "cancelled-task",
    )
    assert event.payload["work_id"] == "" and event.payload["status"] == "cancelled"
