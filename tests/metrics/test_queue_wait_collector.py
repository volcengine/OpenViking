# Copyright (c) 2026 Beijing Volcano Engine Technology Co. Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the ``queue.wait`` event → histogram mapping (#4578)."""

from openviking.metrics.collectors.queue_wait import QueueWaitCollector
from openviking.metrics.core.registry import MetricRegistry
from openviking.metrics.exporters.prometheus import PrometheusExporter


def _render(events):
    registry = MetricRegistry()
    collector = QueueWaitCollector()
    for name, payload in events:
        collector.receive_hook(name, payload, registry)
    return PrometheusExporter(registry=registry).render()


def test_queue_wait_event_renders_histogram():
    text = _render([("queue.wait", {"queue": "Semantic", "wait_seconds": 42.0})])
    assert "openviking_queue_wait_seconds_bucket" in text
    assert 'queue="Semantic"' in text
    assert 'le="60.0"' in text  # 42s observation lands in the 60s bucket


def test_queue_wait_ignores_missing_fields_and_bad_values():
    text = _render(
        [
            ("queue.wait", {"queue": "Semantic"}),  # missing wait_seconds
            ("queue.wait", {"wait_seconds": 1.0}),  # missing queue
            ("queue.wait", {"queue": "E", "wait_seconds": "soon"}),  # non-numeric
            ("queue.wait", {"queue": "E", "wait_seconds": -5.0}),  # negative
            ("http.request", {}),  # unsupported event name
        ]
    )
    assert "openviking_queue_wait_seconds_bucket" not in text


def test_queue_wait_per_queue_labels():
    text = _render(
        [
            ("queue.wait", {"queue": "Semantic", "wait_seconds": 1.0}),
            ("queue.wait", {"queue": "SessionCommit", "wait_seconds": 7200.0}),
        ]
    )
    assert 'queue="Semantic"' in text
    assert 'queue="SessionCommit"' in text
    assert 'le="7200.0"' in text
