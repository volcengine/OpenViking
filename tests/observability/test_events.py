# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.observability.events import (
    _GLOBAL_EVENT_BUS,
    ObservabilityEvent,
    ObservabilityEventBus,
    register_event_subscriber,
    try_publish_event,
)
from openviking.observability.reporting.runtime import make_subscriber
from openviking.observability.reporting.token_sources import bind_dashboard_token_source_type
from openviking.telemetry import bind_ingestion_source_type
from openviking.telemetry.span_models import RootSpanAttributes


def test_event_bus_fans_out_and_enriches_root_metadata():
    _GLOBAL_EVENT_BUS.clear()
    seen_a: list[ObservabilityEvent] = []
    seen_b: list[ObservabilityEvent] = []
    register_event_subscriber("a", seen_a.append)
    register_event_subscriber("b", seen_b.append)

    root = RootSpanAttributes(
        http_method="GET",
        http_route="/api/v1/demo",
        request_id="req-1",
        account_id="acct-1",
        user_id="user-1",
    )
    token = bind_root_observability_context(root)
    try:
        try_publish_event("demo.event", {"value": 1})
    finally:
        reset_root_observability_context(token)
        _GLOBAL_EVENT_BUS.clear()

    assert len(seen_a) == 1
    assert len(seen_b) == 1
    assert seen_a[0].event_name == "demo.event"
    assert seen_a[0].payload == {"value": 1}
    assert seen_a[0].request_id == "req-1"
    assert seen_a[0].account_id == "acct-1"
    assert seen_a[0].user_id == "user-1"


def test_metric_datasource_publishes_to_shared_bus_without_metrics_init():
    _GLOBAL_EVENT_BUS.clear()
    seen: list[ObservabilityEvent] = []
    exported = []
    register_event_subscriber("usage-audit-test", seen.append)
    register_event_subscriber(
        "reporting-test",
        make_subscriber(
            SimpleNamespace(report=exported.append),
            {"telemetry": {"deployment": {"account_id": "cloud", "resource_id": "library"}}},
        ),
    )
    try:
        EventMetricDataSource._emit(
            "vlm.call",
            {
                "account_id": "tenant",
                "user_id": "alice",
                "prompt_tokens": 3,
                "cost_bearer": "customer",
                "api_key": "private-key",
                "prompt": "private-content",
            },
        )
    finally:
        _GLOBAL_EVENT_BUS.clear()

    assert len(seen) == 1
    assert seen[0].event_name == "vlm.call"
    assert len(exported) == 1
    event = exported[0]
    assert (event["account_id"], event["ov_account_id"], event["ov_user_id"]) == (
        "cloud",
        "tenant",
        "alice",
    )
    assert event["prompt_tokens"] == 3 and event["cost_bearer"] == "customer"
    assert "api_key" not in event and "prompt" not in event


@pytest.mark.parametrize(
    ("event_name", "explicit_source", "task_type", "error_code", "expected_source"),
    [
        ("embedding.call", "semantic_search", None, "OK", "semantic_search"),
        ("embedding.call", "skill_search", None, "timeout", "skill_search"),
        ("vlm.call", None, "session_commit", "timeout", "session_commit"),
        ("vlm.call", None, "admin_reindex", "OK", "reindex"),
        ("embedding.call", None, None, "OK", "unknown"),
        ("vlm.call", None, None, "timeout", "unknown"),
        ("understanding_call", "document", None, "OK", None),
    ],
)
def test_dashboard_export_enriches_its_copy_only(
    event_name, explicit_source, task_type, error_code, expected_source
):
    bus = ObservabilityEventBus()
    exported, original = [], []
    bus.register(
        "dashboard",
        make_subscriber(SimpleNamespace(report=exported.append), {}),
    )
    bus.register("raw-event", original.append)
    raw_payload = {"task_type": task_type, "source_type": None, "error_code": error_code}
    with bind_dashboard_token_source_type(explicit_source):
        bus.publish(ObservabilityEvent(event_name=event_name, payload=raw_payload))

    assert exported[0]["source_type"] == expected_source
    assert exported[0]["error_code"] == error_code
    assert original[0].payload["source_type"] is None


def test_dashboard_export_respects_existing_ingestion_sources():
    exported = []
    subscriber = make_subscriber(SimpleNamespace(report=exported.append), {})
    with (
        bind_dashboard_token_source_type("content_write"),
        bind_ingestion_source_type("repository"),
    ):
        subscriber(ObservabilityEvent(event_name="vlm.call", payload={"source_type": "document"}))
        subscriber(ObservabilityEvent(event_name="vlm.call", payload={"error_code": "timeout"}))

    assert [item["source_type"] for item in exported] == ["document", "repository"]
