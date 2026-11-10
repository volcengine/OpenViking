# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace

from openviking.metrics.datasources.base import EventMetricDataSource
from openviking.observability.context import (
    bind_root_observability_context,
    reset_root_observability_context,
)
from openviking.observability.events import (
    _GLOBAL_EVENT_BUS,
    ObservabilityEvent,
    register_event_subscriber,
    try_publish_event,
)
from openviking.observability.reporting.runtime import make_subscriber
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


def test_model_attempt_diagnostics_preserve_errors_and_scope(caplog, monkeypatch):
    import json
    import logging

    import pytest

    from openviking.observability.model_calls import (
        bind_model_call_node,
        diagnostic_fields,
        model_call_diagnostic,
    )
    from openviking.service.task_work_index import bind_task_context

    monkeypatch.setattr(logging.getLogger("openviking"), "propagate", True)
    caplog.set_level("INFO")
    error = ValueError("private credential and request body")
    with bind_task_context("task-a", "account-a", "alice"):
        with bind_model_call_node("account-a", "viking://resources/a.md", "file"):
            with model_call_diagnostic(
                provider="volcengine", model="model-a", timeout_seconds=600, input_chars=50
            ):
                pass
            with pytest.raises(ValueError) as caught:
                with model_call_diagnostic(
                    provider="volcengine", model="model-a", timeout_seconds=600, input_chars=50
                ):
                    raise error
            assert caught.value is error
        assert diagnostic_fields()["uri"] == ""
    finished = [
        json.loads(record.message.split("] ", 1)[1])
        for record in caplog.records
        if record.message.startswith("[VLMCallFinished]")
    ]
    assert len(finished) == 2
    assert {row["status"] for row in finished} == {"completed", "failed"}
    assert len({row["call_id"] for row in finished}) == 2
    assert all(row["task_id"] == "task-a" and row["account_id"] == "account-a" for row in finished)
    assert all(row["uri"] == "viking://resources/a.md" for row in finished)
    assert all(row["duration_ms"] >= 0 for row in finished)
    assert "private credential" not in caplog.text


async def test_model_permit_diagnostics_cancel_without_taking_slot(caplog, monkeypatch):
    import asyncio
    import logging

    import pytest

    from openviking.observability.model_calls import model_permit_diagnostic

    monkeypatch.setattr(logging.getLogger("openviking"), "propagate", True)
    caplog.set_level("INFO")
    semaphore = asyncio.Semaphore(0)

    async def wait():
        async with model_permit_diagnostic(semaphore):
            raise AssertionError("a cancelled waiter must not acquire a slot")

    task = asyncio.create_task(wait())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "SemanticVLMPermitWait" in caplog.text
    assert "SemanticVLMPermitAcquired" not in caplog.text
    semaphore.release()
    async with model_permit_diagnostic(semaphore):
        pass
    assert "SemanticVLMPermitAcquired" in caplog.text
