# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.observability.context import (
    bind_background_observability_context,
    bind_root_observability_context,
    get_root_observability_context,
    reset_root_observability_context,
)
from openviking.observability.events import (
    _GLOBAL_EVENT_BUS,
    ObservabilityEvent,
    build_event,
    register_event_subscriber,
    try_publish_event,
)
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
    register_event_subscriber("usage-audit-test", seen.append)
    try:
        EventMetricDataSource._emit("demo.metric", {"value": 3})
    finally:
        _GLOBAL_EVENT_BUS.clear()

    assert len(seen) == 1
    assert seen[0].event_name == "demo.metric"
    assert seen[0].payload == {"value": 3}


def test_background_context_enriches_model_event_and_restores_previous_root():
    sentinel = RootSpanAttributes(
        http_method="GET",
        http_route="/sentinel",
        request_id="sentinel-request",
        account_id="sentinel-account",
        user_id="sentinel-user",
    )
    sentinel_token = bind_root_observability_context(sentinel)
    try:
        with bind_background_observability_context(
            http_method="QUEUE",
            http_route="/queuefs/test",
            request_id="queue-request",
            url_path="viking://resources/test",
            account_id="queue-account",
            user_id="queue-user",
        ):
            event = build_event("vlm.call", {"prompt_tokens": 3})

        assert event.request_id == "queue-request"
        assert event.account_id == "queue-account"
        assert event.user_id == "queue-user"
        assert get_root_observability_context() is sentinel
    finally:
        reset_root_observability_context(sentinel_token)
