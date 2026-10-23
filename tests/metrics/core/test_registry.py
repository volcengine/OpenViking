# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import re
from pathlib import Path

import pytest

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.core.registry import DEFAULT_LATENCY_BUCKETS, MetricRegistry
from openviking.metrics.exporters.otel import OTelMetricExporter


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
    with pytest.raises(ValueError, match="bucket_schema is reserved"):
        registry.histogram("latency", label_names=("bucket_schema",))


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


def test_histogram_boundary_bucket(registry, render_prometheus):
    durations = (0.05, 20.0, 60.0, 60.001, 120.0, 3600.0, 404261.58497)
    h = registry.histogram("openviking_latency_seconds")
    for duration in durations:
        h.observe(duration)
    text = render_prometheus(registry)
    schema = re.search(r'bucket_schema="([0-9a-f]{16})"', text).group(1)
    assert DEFAULT_LATENCY_BUCKETS[-1] >= max(durations)
    for bound in DEFAULT_LATENCY_BUCKETS:
        count = sum(value <= bound for value in durations)
        assert (
            f'openviking_latency_seconds_bucket{{bucket_schema="{schema}",le="{bound}"}} {count}'
            in text
        )
    assert f'openviking_latency_seconds_count{{bucket_schema="{schema}"}} 7' in text
    request = OTelMetricExporter(registry=registry, enabled=False)._build_export_request()
    point = request.resource_metrics[0].scope_metrics[0].metrics[0].histogram.data_points[0]
    assert tuple(point.explicit_bounds) == DEFAULT_LATENCY_BUCKETS
    assert point.count == sum(point.bucket_counts) == len(durations)
    assert point.bucket_counts[-1] == 0
    assert point.sum == pytest.approx(sum(durations))
    assert {a.key: a.value.string_value for a in point.attributes} == {"bucket_schema": schema}

    legacy = MetricRegistry()
    legacy.histogram("openviking_latency_seconds", buckets=(0.05, 0.1, 10.0)).observe(60.0)
    legacy_schema = re.search(r'bucket_schema="([0-9a-f]{16})"', render_prometheus(legacy)).group(1)
    assert legacy_schema != schema


def test_collectors_do_not_embed_openviking_metric_name_literals():
    root = Path(__file__).resolve().parents[3] / "openviking" / "metrics" / "collectors"
    offenders = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"openviking_' in text or "'openviking_" in text:
            offenders.append(path.name)
    assert offenders == []


def test_registry_absolute_values_and_deletion():
    """Use real families with fixed inputs to verify replacement and deletion; return None."""
    registry = MetricRegistry(max_series_per_metric=1)
    labels = {"plugin": "memfs"}
    for value in (10, 12, 3, 0):
        registry.set_counter("calls_total", value, labels=labels, label_names=("plugin",))
        registry.set_histogram(
            "latency_seconds",
            bucket_bounds=(0.001,),
            bucket_counts=(value, 0),
            count=value,
            value_sum=value * 0.001,
            labels=labels,
            label_names=("plugin",),
        )
        assert dict(registry.iter_counters())["calls_total"] == [(tuple(labels.items()), value)]
        assert list(registry.iter_histograms())[0][3] == [
            (tuple(labels.items()), (value, 0), value, value * 0.001)
        ]
    registry.inc_counter("calls_total", labels=labels, label_names=("plugin",))
    registry.observe_histogram(
        "latency_seconds",
        0.002,
        labels=labels,
        label_names=("plugin",),
        buckets=(0.001,),
    )
    assert list(registry.iter_histograms())[0][3][0][1:] == ((0, 1), 1, 0.002)
    registry.set_counter("calls_total", 5, labels={"plugin": "other"}, label_names=("plugin",))
    assert dict(registry.iter_counters())["calls_total"][0][1] == 1
    assert dict(registry.iter_dropped_series()) == {"calls_total": 1}
    for name, method in (
        ("calls_total", registry.counter_delete_matching),
        ("latency_seconds", registry.histogram_delete_matching),
    ):
        method(name, match_labels={"plugin": "other"})
        method(name, match_labels=labels)
        method(name, match_labels=labels)
    assert list(registry.iter_counters()) == []
    assert list(registry.iter_histograms()) == []


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_registry_rejects_invalid_absolute_counter(value):
    """Reject invalid counter input without replacing the previous value; return None."""
    registry = MetricRegistry()
    registry.set_counter("calls_total", 4)
    with pytest.raises(ValueError):
        registry.set_counter("calls_total", value)
    assert dict(registry.iter_counters())["calls_total"] == [((), 4)]
    empty = MetricRegistry()
    with pytest.raises(ValueError):
        empty.set_counter("calls_total", value)
    assert list(empty.iter_counters()) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"bucket_bounds": (1.0, 0.0)},
        {"bucket_bounds": (float("inf"),)},
        {"bucket_counts": (1,)},
        {"bucket_counts": (-1, 2)},
        {"count": 2},
        {"count": True},
        {"bucket_counts": (True, 0)},
        {"value_sum": float("nan")},
    ],
)
def test_registry_rejects_invalid_absolute_histogram(changes):
    """Reject malformed histogram input without corrupting an existing series; return None."""
    registry = MetricRegistry()
    values = {
        "bucket_bounds": (0.001,),
        "bucket_counts": (1, 0),
        "count": 1,
        "value_sum": 0.001,
    }
    registry.set_histogram("latency_seconds", **values)
    before = list(registry.iter_histograms())
    with pytest.raises(ValueError):
        registry.set_histogram("latency_seconds", **(values | changes))
    assert list(registry.iter_histograms()) == before
    empty = MetricRegistry()
    with pytest.raises(ValueError):
        empty.set_histogram("latency_seconds", **(values | changes))
    assert list(empty.iter_histograms()) == []
    empty.set_histogram("latency_seconds", **values)
    assert list(empty.iter_histograms()) == before


def test_registry_deletion_hides_empty_families_but_keeps_contract(render_prometheus):
    """Delete all series for each type and verify export absence and schema retention."""
    registry = MetricRegistry()
    registry.counter("untouched_total")
    registry.gauge("untouched_gauge")
    registry.histogram("untouched_histogram")
    registry.counter_delete_matching("untouched_total", match_labels={})
    registry.gauge_delete_matching("untouched_gauge", match_labels={})
    registry.histogram_delete_matching("untouched_histogram", match_labels={})
    assert "untouched_total 0" in render_prometheus(registry)
    registry.set_counter("removed_total", 1)
    registry.set_gauge("removed_gauge", 1)
    registry.set_histogram(
        "removed_histogram",
        bucket_bounds=[1],
        bucket_counts=[1, 0],
        count=1,
        value_sum=1,
    )
    for kind, name in (
        ("counter", "removed_total"),
        ("gauge", "removed_gauge"),
        ("histogram", "removed_histogram"),
    ):
        getattr(registry, f"{kind}_delete_matching")(name, match_labels={})
    text = render_prometheus(registry)
    assert "removed_" not in text
    assert "untouched_total 0" in text and "untouched_gauge 0" in text
    assert re.search(r'untouched_histogram_count\{bucket_schema="[0-9a-f]{16}"\} 0', text)
    exporter = OTelMetricExporter(registry=registry, enabled=False)
    request = exporter._build_export_request()
    metrics = {m.name: m for m in request.resource_metrics[0].scope_metrics[0].metrics}
    assert set(metrics) == {"untouched_total", "untouched_gauge", "untouched_histogram"}
    assert metrics["untouched_total"].sum.data_points[0].as_int == 0
    assert metrics["untouched_gauge"].gauge.data_points[0].as_int == 0
    assert metrics["untouched_histogram"].histogram.data_points[0].count == 0
    with pytest.raises(ValueError):
        registry.gauge("removed_total")
    with pytest.raises(ValueError):
        registry.histogram("removed_histogram", buckets=[2])
    with pytest.raises(ValueError):
        registry.counter("removed_total").set(-1)
    with pytest.raises(ValueError):
        registry.histogram("removed_histogram", buckets=[1]).set((1,), 1, 1)
    assert "removed_" not in render_prometheus(registry)
    registry.set_counter("removed_total", 2)
    registry.gauge("removed_gauge").inc(2)
    registry.histogram("removed_histogram", buckets=[1]).observe(0.5)
    text = render_prometheus(registry)
    assert "removed_total 2" in text
    assert "removed_gauge 2.0" in text
    assert re.search(r'removed_histogram_count\{bucket_schema="[0-9a-f]{16}"\} 1', text)
    request = exporter._build_export_request()
    metrics = {m.name: m for m in request.resource_metrics[0].scope_metrics[0].metrics}
    assert metrics["removed_total"].sum.data_points[0].as_int == 2
    assert metrics["removed_gauge"].gauge.data_points[0].as_int == 2
    assert metrics["removed_histogram"].histogram.data_points[0].count == 1


@pytest.mark.parametrize("kind", ("counter", "gauge", "histogram"))
def test_registry_partial_deletion_keeps_active_series(kind, render_prometheus):
    """Delete selected labels for kind; retain active values and the label contract."""
    registry = MetricRegistry()
    options = {"buckets": [1]} if kind == "histogram" else {}
    handle = getattr(registry, kind)("active", label_names=("plugin",), **options)
    for plugin in ("removed", "kept"):
        if kind == "histogram":
            handle.observe(0.5, labels={"plugin": plugin})
        else:
            handle.inc(labels={"plugin": plugin})
    delete = getattr(registry, f"{kind}_delete_matching")
    delete("active", match_labels={"plugin": "removed"})
    text = render_prometheus(registry)
    assert 'plugin="removed"' not in text
    sample = "active_count" if kind == "histogram" else "active"
    label_pattern = 'plugin="kept"'
    if kind == "histogram":
        label_pattern = 'bucket_schema="[0-9a-f]{16}",' + label_pattern
    assert re.search(rf"{sample}\{{{label_pattern}\}} 1", text)
    delete("active", match_labels={"plugin": "kept"})
    assert "active" not in render_prometheus(registry)
    with pytest.raises(ValueError):
        getattr(registry, kind)("active", label_names=("other",), **options)
    if kind == "histogram":
        handle.set((0, 0), 0, 0, labels={"plugin": "kept"})
    else:
        handle.set(0, labels={"plugin": "kept"})
    assert re.search(rf"{sample}\{{{label_pattern}\}} 0", render_prometheus(registry))


def test_registry_handles_validate_absolute_values(render_prometheus):
    """Call public handles with integer and invalid inputs and verify validated storage."""
    registry = MetricRegistry()
    counter = registry.counter("calls_total")
    counter.set(1)
    assert "calls_total 1" in render_prometheus(registry)
    for value in (-1, float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError):
            counter.set(value)
        assert dict(registry.iter_counters())["calls_total"] == [((), 1)]
    histogram = registry.histogram("latency", buckets=[1])
    histogram.set([1, 0], 1, 1)
    assert re.search(
        r'latency_sum\{bucket_schema="[0-9a-f]{16}"\} 1.0', render_prometheus(registry)
    )
    before = list(registry.iter_histograms())
    for counts, count, value_sum in (
        ((1,), 1, 1),
        ((-1, 2), 1, 1),
        ((True, 0), 1, 1),
        ((1.0, 0), 1, 1),
        ((1, 0), True, 1),
        ((1, 0), 2, 1),
        ((1, 0), 1, -1),
        ((1, 0), 1, float("inf")),
        ((1, 0), 1, float("nan")),
    ):
        with pytest.raises(ValueError):
            histogram.set(counts, count, value_sum)
        assert list(registry.iter_histograms()) == before
    for bounds in ((-1,), (float("inf"),), (float("nan"),), (1, 1), (2, 1)):
        invalid = MetricRegistry()
        handle = invalid.histogram("invalid", buckets=bounds)
        with pytest.raises(ValueError):
            handle.set((0,) * (len(bounds) + 1), 0, 0)
        assert list(invalid.iter_histograms())[0][3] == []
