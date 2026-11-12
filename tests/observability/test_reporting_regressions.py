"""Regression coverage for exporter delivery and background event attribution."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.metrics.collectors.embedding import EmbeddingCollector
from openviking.metrics.collectors.vlm import VLMCollector
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.observability.events import build_event
from openviking.observability.reporting import runtime
from openviking.observability.reporting.facts import begin_request_reporting
from openviking.observability.reporting.reporter import KafkaEventReporter
from openviking.service.task_domain import TaskRecord
from openviking.service.task_store import TaskCreationResult
from openviking.service.task_tracker import TaskTracker
from openviking.service.task_work_index import bind_task_context, get_task_context
from openviking.telemetry.span_models import RootSpanAttributes
from openviking_cli.utils.async_utils import run_async


def _root():
    root = RootSpanAttributes(http_method="POST", http_route="/test", request_id="request")
    root.account_id, root.user_id = "account", "user"
    return root


def test_reentrant_run_async_preserves_task_and_root_context():
    async def child():
        return build_event("vlm.call", {})

    async def outer():
        token = bind_root_observability_context(_root())
        try:
            with bind_task_context("task", "account", "user", task_type="session_commit"):
                parent = get_task_context()
                event = run_async(child())
                assert get_task_context() is parent
                assert (event.account_id, event.user_id, event.payload["task_id"]) == (
                    "account",
                    "user",
                    "task",
                )
                assert event.request_id == "request"
                assert event.payload["usage_purpose"] == "memory_extract"
            assert get_task_context() is None
        finally:
            reset_root_observability_context(token)

    run_async(outer())


async def test_store_create_replay_remembers_http_task_without_modifying_state(monkeypatch):
    monkeypatch.setattr(runtime, "_reporter", object())
    stored = TaskRecord(
        task_id="existing-task",
        task_type="content_write",
        resource_id="viking://resources/document.md",
        account_id="account",
        user_id="user",
        meta={"mode": "create"},
    )
    store = SimpleNamespace(
        get_task=AsyncMock(return_value=None),
        create=AsyncMock(return_value=TaskCreationResult(stored, False)),
    )
    tracker = TaskTracker(store)
    token = bind_root_observability_context(_root())
    try:
        begin_request_reporting()
        replayed = await tracker.create(
            "content_write", account_id="account", user_id="user", task_id=stored.task_id
        )
        event = build_event("http.request", {})
        assert replayed.status == stored.status and replayed.created_at == stored.created_at
        assert event.payload["metric_tasks"][0]["task_id"] == stored.task_id
        assert event.payload["metric_tasks"][0]["mode"] == "create"
        assert store.create.await_count == 1
    finally:
        reset_root_observability_context(token)


@pytest.mark.parametrize("collector_type", [VLMCollector, EmbeddingCollector])
@pytest.mark.parametrize("error_code", ["OK", "timeout"])
def test_collectors_count_unknown_usage_calls_without_token_accumulation(
    collector_type, error_code
):
    registry = SimpleNamespace(inc_counter=Mock(), observe_histogram=Mock())
    collector = collector_type()
    name = "vlm.call" if collector_type is VLMCollector else "embedding.call"
    collector.receive_hook(
        name,
        {
            "provider": "openai",
            "model_name": "test-model",
            "duration_seconds": 0.25,
            "prompt_tokens": None,
            "completion_tokens": None,
            "account_id": "account",
            "error_code": error_code,
        },
        registry,
    )
    assert registry.inc_counter.call_count == 1
    assert registry.inc_counter.call_args.args == (collector.CALLS_TOTAL,)
    assert registry.observe_histogram.call_count == 1
    assert registry.observe_histogram.call_args.args == (collector.CALL_DURATION_SECONDS, 0.25)
    assert registry.inc_counter.call_args.kwargs["account_id"] == "account"


class _Producer:
    def __init__(self, *, sync_failures=0, ack_error=None):
        self.sync_failures = sync_failures
        self.ack_error = ack_error
        self.values = []
        self.timeouts = []
        self.closed = False

    def send(self, topic, *, key, value):
        self.values.append((topic, key, value))
        if self.sync_failures:
            self.sync_failures -= 1
            raise OSError("temporary enqueue failure")

        def get(timeout):
            self.timeouts.append(timeout)
            if self.ack_error:
                raise self.ack_error

        return SimpleNamespace(get=get)

    def close(self, timeout):
        self.closed = True


def test_worker_sends_bounded_batch_before_waiting_for_any_ack(monkeypatch):
    # Queue both observations before starting the actual worker loop.
    monkeypatch.setattr(threading.Thread, "start", lambda _: None)
    producer = _Producer()
    original_send = producer.send

    def send(*args, **kwargs):
        future = original_send(*args, **kwargs)

        def get(timeout):
            assert len(producer.values) == 2
            future.get(timeout=timeout)

        return SimpleNamespace(get=get)

    monkeypatch.setattr(producer, "send", send)
    reporter = KafkaEventReporter(
        {"kafka": {"topic": "events"}, "telemetry": {"queue_capacity": 2}}, producer=producer
    )
    assert reporter.report({"event_id": "first"})
    assert reporter.report({"event_id": "second"})
    reporter._closed = True
    reporter._run()
    assert reporter.sent == 2 and reporter.failed == 0
    assert reporter.queue.unfinished_tasks == 0
    assert producer.closed
    assert [json.loads(value)["event_id"] for _, _, value in producer.values] == ["first", "second"]
    assert all(timeout >= 20 for timeout in producer.timeouts)


def test_synchronous_send_retry_preserves_exact_serialized_observation():
    producer = _Producer(sync_failures=1)
    reporter = KafkaEventReporter({"kafka": {"topic": "events"}}, producer=producer)
    try:
        assert reporter.report({"event_id": "unchanged-id", "value": 1})
        reporter.flush(timeout=2)
        assert len(producer.values) == 2 and producer.values[0] == producer.values[1]
        assert reporter.sent == 1 and reporter.failed == 0
        assert producer.timeouts[0] >= 20
    finally:
        reporter.close(timeout=2)


def test_lazy_producer_initialization_retry_retains_event(monkeypatch):
    producer = _Producer()
    reporter = KafkaEventReporter({"kafka": {"topic": "events"}}, producer=producer)
    factory = Mock(side_effect=[OSError("temporary initialization failure"), producer])
    reporter.producer = None
    monkeypatch.setattr(reporter, "_producer_factory", factory, raising=False)
    try:
        assert reporter.report({"event_id": "retained"})
        reporter.flush(timeout=2)
        assert factory.call_count == 2
        assert json.loads(producer.values[0][2])["event_id"] == "retained"
        assert reporter.sent == 1 and reporter.failed == 0
    finally:
        reporter.close(timeout=2)


def test_ack_timeout_is_not_retried_and_remains_visible_to_close():
    producer = _Producer(ack_error=TimeoutError("ambiguous acknowledgment"))
    reporter = KafkaEventReporter({"kafka": {"topic": "events"}}, producer=producer)
    assert reporter.report({"event_id": "not-resubmitted"})
    with pytest.raises(RuntimeError, match="incomplete"):
        reporter.flush(timeout=2)
    with pytest.raises(RuntimeError, match="lost events"):
        reporter.close(timeout=2)
    assert len(producer.values) == 1 and reporter.failed == 1 and reporter.sent == 0
    assert producer.timeouts[0] >= 20 and producer.closed


def test_dropped_serialization_failure_makes_flush_and_close_unsuccessful():
    producer = _Producer()
    reporter = KafkaEventReporter({"kafka": {"topic": "events"}}, producer=producer)
    assert not reporter.report({"value": float("nan")})
    with pytest.raises(RuntimeError, match="incomplete"):
        reporter.flush(timeout=2)
    with pytest.raises(RuntimeError, match="lost events"):
        reporter.close(timeout=2)
    assert reporter.dropped == 1 and reporter.failed == 0
    assert producer.values == [] and producer.closed
