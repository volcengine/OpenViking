from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.observability.events import build_event, get_event_bus, register_event_subscriber
from openviking.observability.reporting import runtime
from openviking.observability.reporting.facts import (
    begin_request_reporting,
    bind_memory_type,
    defer_file_changes,
    emit_file_change,
    remember_request_task,
)
from openviking.observability.reporting.inventory import inventory_lines
from openviking.observability.reporting.reporter import KafkaEventReporter
from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import (
    bind_task_context,
    bind_task_context_from_message,
    get_task_context,
    prepare_task_payload,
)
from openviking.storage.viking_fs._ops import _OpsMixin
from openviking.telemetry.span_models import RootSpanAttributes
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def isolated_events():
    get_event_bus().clear()
    yield
    get_event_bus().clear()


class Capture:
    def __init__(self):
        self.events = []

    def report(self, payload):
        self.events.append(json.loads(json.dumps(payload)))
        return True


def test_understanding_export_preserves_task_identity_without_fabricated_usage():
    capture = Capture()
    with bind_task_context("task_parse", "space_one", "user_one", task_type="add_resource"):
        event = build_event(
            "understanding_call",
            {"provider": "understanding_api", "status": "ok", "usage_purpose": "resource_parse"},
        )
    runtime.make_subscriber(
        capture,
        {
            "telemetry": {
                "deployment": {
                    "account_id": "cloud_one",
                    "resource_id": "library_one",
                    "region": "cn-beijing",
                }
            }
        },
    )(event)
    payload = capture.events[0]
    assert (payload["ov_account_id"], payload["ov_user_id"], payload["task_id"]) == (
        "space_one",
        "user_one",
        "task_parse",
    )
    assert payload["event_name"] == "understanding_call"
    assert "prompt_tokens" not in payload and "completion_tokens" not in payload


def test_export_preserves_cloud_and_ov_identity_and_drops_private_payload():
    capture = Capture()
    subscriber = runtime.make_subscriber(
        capture,
        {
            "telemetry": {
                "deployment": {
                    "account_id": "cloud_account",
                    "resource_id": "cloud_library",
                    "region": "cn-beijing",
                }
            }
        },
    )
    subscriber(
        build_event(
            "vlm.call",
            {
                "account_id": "ov_space",
                "user_id": "ov_user",
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "api_key": "do_not_export",
                "prompt": "private_content",
                "task_id": "task_one",
            },
        )
    )
    event = capture.events[0]
    assert (
        event["account_id"],
        event["resource_id"],
        event["ov_account_id"],
        event["ov_user_id"],
    ) == ("cloud_account", "cloud_library", "ov_space", "ov_user")
    assert event["event_name"] == "vlm_call" and event["cost_bearer"] == "unknown"
    assert "api_key" not in event and "prompt" not in event
    assert event["prompt_tokens"] == 11 and event["completion_tokens"] == 7


def test_existing_task_metadata_survives_queue_serialization():
    with bind_task_context(
        "task_one", "space_one", "user_one", task_type="content_write", mode="create"
    ):
        payload, _ = prepare_task_payload({"message": "business_payload"})
    wire = json.loads(json.dumps(payload))
    with bind_task_context_from_message({"data": wire}):
        event = build_event("file_mutation", {"uri": "viking://resources/doc.md"})
    assert (event.account_id, event.user_id, event.payload["task_id"]) == (
        "space_one",
        "user_one",
        "task_one",
    )
    assert event.payload["task_type"] == "content_write" and event.payload["mode"] == "create"


@pytest.mark.parametrize(
    ("method", "route", "expected_task"),
    [
        ("QUEUE", "/queuefs/session_commit", "persisted_task"),
        ("POST", "/queuefs/session_commit", None),
        ("QUEUE", "/queuefs/unrelated", None),
    ],
)
def test_session_queue_retains_task_attribution_without_task_work_context(
    method, route, expected_task
):
    root = RootSpanAttributes(http_method=method, http_route=route, request_id="persisted_task")
    root.account_id = "space_one"
    root.user_id = "user_one"
    token = bind_root_observability_context(root)
    try:
        event = build_event("vlm.call", {"prompt_tokens": 3, "completion_tokens": 2})
        assert event.payload.get("task_id") == expected_task
        if expected_task:
            assert event.payload["task_type"] == "session_commit"
            assert event.payload["usage_purpose"] == "memory_extract"
            assert (event.account_id, event.user_id) == ("space_one", "user_one")
    finally:
        reset_root_observability_context(token)


def test_memory_operation_provenance_replaces_stale_batch_queue_identity():
    root = RootSpanAttributes(
        http_method="QUEUE", http_route="/queuefs/session_commit", request_id="first_commit"
    )
    root.account_id, root.user_id = "space_one", "user_one"
    token = bind_root_observability_context(root)
    try:
        assert get_task_context() is None
        with bind_memory_type("preferences", task_id="second_commit"):
            event = build_event(
                "file_mutation",
                {"account_id": "space_one", "user_id": "user_one", "change_type": "modified"},
            )
        assert event.payload["task_id"] == "second_commit"
        assert event.request_id == "second_commit"
        assert event.payload["task_type"] == "session_commit"
        assert get_task_context() is None
        restored = build_event("file_mutation", {})
        assert restored.payload["task_id"] == "first_commit"
    finally:
        reset_root_observability_context(token)


@pytest.mark.parametrize(
    "memory_type,leaf",
    [
        ("cases", "cases/sample.md"),
        ("experiences", "experiences/sample.md"),
        ("profile", "profile.md"),
        ("identity", "identity.md"),
        ("soul", "soul.md"),
    ],
)
def test_committed_manual_memory_update_waits_for_root_task(monkeypatch, memory_type, leaf):
    monkeypatch.setattr(runtime, "_reporter", object())
    captured = []
    register_event_subscriber("manual_write", captured.append)
    ctx = RequestContext(
        user=UserIdentifier(account_id="space_one", user_id="user_one"), role=Role.USER
    )
    with defer_file_changes() as pending:
        emit_file_change("viking://user/user_one/memories/" + leaf, ctx, "modified")
        assert not captured
        written_at = pending.events[0].timestamp
        pending.publish(
            SimpleNamespace(
                task_id="write_task", task_type="content_write", meta={"mode": "append"}
            )
        )
    assert len(captured) == 1
    event = captured[0]
    assert event.timestamp == written_at
    assert event.payload["task_id"] == "write_task"
    assert event.payload["memory_type"] == memory_type
    assert event.payload["change_type"] == "modified"


def test_aborted_manual_write_does_not_publish_deferred_facts(monkeypatch):
    monkeypatch.setattr(runtime, "_reporter", object())
    captured = []
    register_event_subscriber("failed_write", captured.append)
    with pytest.raises(RuntimeError), defer_file_changes():
        emit_file_change("viking://resources/failed.md", None, "added")
        raise RuntimeError("commit failed")
    assert not captured


def test_multiple_exporters_share_event_and_task_fanout_ids(monkeypatch):
    monkeypatch.setattr(runtime, "_reporter", object())
    first, second = Capture(), Capture()
    event = build_event(
        "http.request",
        {
            "status": "200",
            "metric_tasks": [
                {"task_id": "task_one", "task_type": "content_write", "mode": "create"}
            ],
        },
    )
    runtime.make_subscriber(first, {})(event)
    runtime.make_subscriber(second, {})(event)
    assert first.events[0]["event_id"] == second.events[0]["event_id"]


def test_compile_snapshot_reports_only_committed_business_changes_with_stable_identity(monkeypatch):
    from openviking.observability.reporting.facts import emit_compile_outputs

    monkeypatch.setattr(runtime, "_reporter", object())
    capture = Capture()
    register_event_subscriber("test_compile_export", runtime.make_subscriber(capture, {}))
    target = "viking://user/alice/resources/compiled"
    task = SimpleNamespace(
        task_id="cmp_one",
        task_type="compile",
        account_id="space_one",
        user_id="alice",
        meta={"request": {"to": target}},
    )
    changes = [
        {"uri": target + "/result.md", "change_type": "added"},
        {"uri": target + "/previous.md", "change_type": "modified"},
        {"uri": target + "/removed.md", "change_type": "deleted"},
        {"uri": target + "/.abstract.md", "change_type": "added"},
        {"uri": target + "/.relations.json", "change_type": "added"},
        {"uri": target + "_other/foreign.md", "change_type": "added"},
    ]
    for _ in range(2):
        with bind_task_context("unrelated_task", "other_space", "bob", task_type="content_write"):
            emit_compile_outputs(
                task, changes, snapshot_id="committed_head", committed_at=1793806188000
            )
    assert len(capture.events) == 6
    for first, replay in zip(capture.events[:3], capture.events[3:], strict=True):
        assert first["event_id"] == replay["event_id"]
        assert first["timestamp"] == replay["timestamp"] == 1793806188000
        assert (first["ov_account_id"], first["ov_user_id"], first["task_id"]) == (
            "space_one",
            "alice",
            "cmp_one",
        )
        assert first["task_type"] == "compile" and first["operation"] == "compile_commit"
        assert first["output_role"] == "business_file"
    assert [x["change_type"] for x in capture.events[:3]] == ["added", "modified", "deleted"]
    emit_compile_outputs(task, changes, snapshot_id="different_head", committed_at=1793806188000)
    assert capture.events[0]["event_id"] != capture.events[6]["event_id"]


def test_compile_reporting_failure_cannot_change_business_outcome(monkeypatch):
    from openviking.observability.reporting import facts

    monkeypatch.setattr(runtime, "_reporter", object())
    task = SimpleNamespace(
        task_id="cmp_one",
        task_type="compile",
        account_id="space_one",
        user_id="alice",
        meta={"request": {"to": "viking://resources/compiled"}},
    )

    def fail(*args):
        raise RuntimeError("reporter unavailable")

    monkeypatch.setattr(facts, "try_publish_event", fail)
    facts.emit_compile_outputs(
        task,
        [{"uri": "viking://resources/compiled/result.md", "change_type": "added"}],
        snapshot_id="head",
        committed_at=1793806188000,
    )


def test_model_cost_mapping_respects_space_and_rejects_ambiguous_ownership():
    capture = Capture()
    config = {
        "telemetry": {
            "deployment": {"account_id": "cloud", "resource_id": "library", "region": "region"},
            "model_cost_bearers": [
                {"provider": "provider", "model_name": "model", "cost_bearer": "ov"},
                {
                    "provider": "provider",
                    "model_name": "model",
                    "ov_account_id": "customer_space",
                    "cost_bearer": "customer",
                },
            ],
        }
    }
    send = runtime.make_subscriber(capture, config)
    send(
        build_event(
            "vlm.call",
            {"account_id": "customer_space", "provider": "provider", "model_name": "model"},
        )
    )
    send(
        build_event(
            "vlm.call", {"account_id": "other_space", "provider": "provider", "model_name": "model"}
        )
    )
    assert [event["cost_bearer"] for event in capture.events] == ["customer", "ov"]
    config["telemetry"]["model_cost_bearers"].append(
        {
            "provider": "provider",
            "model_name": "model",
            "ov_account_id": "customer_space",
            "cost_bearer": "ov",
        }
    )
    send(
        build_event(
            "vlm.call",
            {"account_id": "customer_space", "provider": "provider", "model_name": "model"},
        )
    )
    assert capture.events[-1]["cost_bearer"] == "unknown"


def test_deployment_model_cost_owner_is_external_and_does_not_override_specific_rules(monkeypatch):
    monkeypatch.setenv("OV_MODEL_COST_BEARER", "customer")
    capture = Capture()
    subscriber = runtime.make_subscriber(
        capture,
        {
            "telemetry": {
                "model_cost_bearers": [
                    {"provider": "provider", "model_name": "platform_model", "cost_bearer": "ov"}
                ]
            }
        },
    )
    subscriber(build_event("vlm.call", {"provider": "provider", "model_name": "customer_model"}))
    subscriber(
        build_event("embedding.call", {"provider": "provider", "model_name": "platform_model"})
    )
    assert [event["cost_bearer"] for event in capture.events] == ["customer", "ov"]
    monkeypatch.setenv("OV_MODEL_COST_BEARER", "invalid_owner")
    subscriber(build_event("vlm.call", {"provider": "provider", "model_name": "customer_model"}))
    assert capture.events[-1]["cost_bearer"] == "unknown"


@pytest.mark.asyncio
async def test_request_task_attribution_is_isolated_between_concurrent_requests(monkeypatch):
    monkeypatch.setattr(runtime, "_reporter", object())

    async def request(identifier):
        token = bind_root_observability_context(
            RootSpanAttributes(http_method="POST", http_route="/test", request_id=identifier)
        )
        try:
            begin_request_reporting()
            remember_request_task(
                SimpleNamespace(task_id=identifier, task_type="compile", meta={}, created_at=1.0)
            )
            await asyncio.sleep(0)
            return build_event("http.request", {"status": "200"}).payload["metric_tasks"]
        finally:
            reset_root_observability_context(token)

    first, second = await asyncio.gather(request("first"), request("second"))
    assert [task["task_id"] for task in first] == ["first"]
    assert [task["task_id"] for task in second] == ["second"]


@pytest.mark.asyncio
async def test_file_event_occurs_after_success_and_preserves_real_memory_type(monkeypatch):
    monkeypatch.setattr(runtime, "_reporter", object())
    events = []
    register_event_subscriber("test_capture", events.append)

    class FileSystem(_OpsMixin):
        _privacy_sanitizer = None
        _post_write_hook = None
        _ensure_access = AsyncMock()
        _ensure_parent_dirs = AsyncMock()

        def _uri_to_path(self, uri, **kwargs):
            return uri

        def _pathlock_fs_ctx(self, *args):
            return None

    fs = FileSystem()
    fs._async_agfs = SimpleNamespace(
        stat=AsyncMock(side_effect=FileNotFoundError()), write=AsyncMock()
    )
    ctx = RequestContext(user=UserIdentifier("space_one", "user_one"), role=Role.USER)
    with bind_memory_type("custom_template"):
        await fs.write_file(
            "viking://user/user_one/memories/custom/demo.md", "private_body", ctx=ctx
        )
    assert len(events) == 1
    assert events[0].payload["memory_type"] == "custom_template"
    assert events[0].payload["change_type"] == "added"
    assert "private_body" not in json.dumps(events[0].payload)
    fs._async_agfs.write.side_effect = OSError("test write failure")
    with pytest.raises(OSError):
        await fs.write_file("viking://resources/failed.md", "private_body", ctx=ctx)
    assert len(events) == 1


class Producer:
    def __init__(self, gate=None, fail=False):
        self.gate, self.fail, self.closed, self.values = gate, fail, False, []
        self.started = threading.Event()

    def send(self, topic, *, key, value):
        self.values.append((topic, key, value))
        self.started.set()

        def get(timeout):
            if self.gate:
                self.gate.wait(timeout)
            if self.fail:
                raise OSError("test send failure")

        return SimpleNamespace(get=get)

    def close(self, **kwargs):
        self.closed = True


@pytest.mark.parametrize("capacity", [0, -1])
def test_exporter_rejects_configuration_that_would_make_the_queue_unbounded(capacity):
    with pytest.raises(ValueError, match="positive integer"):
        KafkaEventReporter(
            {"kafka": {"topic": "events"}, "telemetry": {"queue_capacity": capacity}},
            producer=Producer(),
        )


def test_shutdown_timeout_does_not_leave_an_unstoppable_worker():
    gate = threading.Event()
    producer = Producer(gate=gate)
    reporter = KafkaEventReporter({"kafka": {"topic": "configured_test_topic"}}, producer=producer)
    event = {"event_id": "one", "value": 1}
    reporter.report(event)
    event["value"] = 2
    with pytest.raises(TimeoutError):
        reporter.close(timeout=0.01)
    gate.set()
    reporter.close(timeout=2)
    assert producer.closed
    assert json.loads(producer.values[0][2])["value"] == 1


@pytest.fixture
def exporter_logs(caplog):
    from openviking.observability.reporting.reporter import logger

    # The application configures its own handlers without propagating to root.
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


def test_failed_delivery_is_visible_to_flush_and_close(exporter_logs):
    reporter = KafkaEventReporter(
        {"kafka": {"topic": "configured_test_topic"}}, producer=Producer(fail=True)
    )
    reporter.report({"event_id": "failed_event"})
    with pytest.raises(RuntimeError):
        reporter.flush(timeout=2)
    with pytest.raises(RuntimeError):
        reporter.close(timeout=2)
    assert reporter.failed == 1
    assert "Event exporter delivery failed" in exporter_logs.text
    assert "failed_event" not in exporter_logs.text


def test_full_queue_does_not_wait_for_the_broker_and_logs_only_counts(exporter_logs):
    gate = threading.Event()
    producer = Producer(gate=gate)
    reporter = KafkaEventReporter(
        {"kafka": {"topic": "events"}, "telemetry": {"queue_capacity": 1}},
        producer=producer,
    )
    event = {"uri": "viking://user/private_member/private_file.md"}
    try:
        assert reporter.report(event)
        assert producer.started.wait(timeout=1)
        assert reporter.report(event)
        assert not reporter.report(event)
        assert reporter.dropped == 1
        assert "Event exporter dropped events" in exporter_logs.text
        assert "private_member" not in exporter_logs.text
    finally:
        gate.set()
        with pytest.raises(RuntimeError, match="lost events"):
            reporter.close(timeout=2)


@pytest.mark.asyncio
async def test_inventory_uses_metadata_only_and_requires_complete_vector_scan():
    manager = SimpleNamespace(
        list_accounts=AsyncMock(return_value=[{"account_id": "space_one"}]),
        list_users_page=AsyncMock(
            return_value={
                "users": [{"user_id": "member", "role": "user", "api_key": "do_not_export"}],
                "total": 1,
            }
        ),
    )

    async def ls(uri, **kwargs):
        if uri == "viking://":
            return [{"uri": "viking://resources", "isDir": True}]
        return [{"uri": "viking://resources/doc.md", "isDir": False, "content": "do_not_export"}]

    async def scan(*args, **kwargs):
        yield {"id": "vector_one", "uri": "viking://resources/doc.md", "level": 2, "vector": [1.0]}

    fs = SimpleNamespace(ls=ls, _get_vector_store=lambda: SimpleNamespace(_strict_scan=scan))
    output = [
        json.loads(line) async for line in inventory_lines(SimpleNamespace(viking_fs=fs), manager)
    ]
    assert output[-1]["status"] == "complete" and output[-1]["row_count"] == len(output) - 1
    assert "do_not_export" not in json.dumps(output)
    assert all("vector" not in row for row in output)
    assert any(row.get("file_uri") == "viking://resources/doc.md" for row in output)
    assert all(row.get("account_id") == "space_one" for row in output[:-1])
    unknown = [
        json.loads(line)
        async for line in inventory_lines(
            SimpleNamespace(viking_fs=fs), manager, account_id="missing_space"
        )
    ]
    assert unknown[-1]["status"] == "failed"

    async def failing_scan(*args, **kwargs):
        raise OSError("partial scan")
        yield

    fs._get_vector_store = lambda: SimpleNamespace(_strict_scan=failing_scan)
    failed = [
        json.loads(line) async for line in inventory_lines(SimpleNamespace(viking_fs=fs), manager)
    ]
    assert failed[-1]["status"] == "failed"
