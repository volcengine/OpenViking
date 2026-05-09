# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

import pytest

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.core.registry import MetricRegistry


def test_metric_collector_metric_name_includes_namespace_and_optional_unit():
    assert (
        MetricCollector.metric_name("cache", "hits", unit="total") == "openviking_cache_hits_total"
    )
    assert (
        MetricCollector.metric_name("resource", "stage_duration", unit="seconds")
        == "openviking_resource_stage_duration_seconds"
    )
    assert MetricCollector.metric_name("lock", "active") == "openviking_lock_active"


def test_registry_rejects_type_conflict():
    registry = MetricRegistry()
    registry.counter("openviking_conflict_total")
    with pytest.raises(ValueError):
        registry.gauge("openviking_conflict_total")


def test_registry_label_keys_must_match_definition():
    registry = MetricRegistry()
    c = registry.counter("openviking_labeled_total", label_names=("a", "b"))
    with pytest.raises(ValueError):
        c.inc(labels={"a": "1"})
    with pytest.raises(ValueError):
        c.inc(labels={"a": "1", "b": "2", "c": "3"})


def test_registry_canonicalizes_label_name_order_for_same_family(render_prometheus):
    registry = MetricRegistry()

    registry.inc_counter(
        "openviking_ordered_total",
        labels={"status": "ok", "provider": "local"},
        label_names=("status", "provider"),
    )
    registry.inc_counter(
        "openviking_ordered_total",
        labels={"provider": "local", "status": "ok"},
        label_names=("provider", "status"),
    )

    text = render_prometheus(registry)
    assert 'openviking_ordered_total{provider="local",status="ok"} 2' in text


def test_counter_only_increases():
    registry = MetricRegistry()
    c = registry.counter("openviking_counter_total")
    c.inc()
    with pytest.raises(ValueError):
        c.inc(amount=0)
    with pytest.raises(ValueError):
        c.inc(amount=-1)


def test_gauge_inc_dec(registry, render_prometheus):
    g = registry.gauge("openviking_gauge")
    g.set(1)
    g.inc()
    g.dec(0.5)
    text = render_prometheus(registry)
    assert "openviking_gauge 1.5" in text


def test_histogram_boundary_bucket(registry, render_prometheus):
    h = registry.histogram("openviking_latency_seconds", buckets=(0.05, 0.1))
    h.observe(0.05)
    text = render_prometheus(registry)
    assert 'openviking_latency_seconds_bucket{le="0.05"} 1' in text
    assert 'openviking_latency_seconds_bucket{le="0.1"} 1' in text
    assert 'openviking_latency_seconds_bucket{le="+Inf"} 1' in text


def test_collectors_do_not_embed_openviking_metric_name_literals():
    root = Path(__file__).resolve().parents[3] / "openviking" / "metrics" / "collectors"
    offenders = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"openviking_' in text or "'openviking_" in text:
            offenders.append(path.name)
    assert offenders == []
