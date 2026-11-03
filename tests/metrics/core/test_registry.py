# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from opentelemetry.exporter.otlp.proto.common.metrics_encoder import encode_metrics
from prometheus_client.parser import text_string_to_metric_families

from openviking.metrics.core.registry import DEFAULT_LATENCY_BUCKETS, MetricRegistry


def test_registry_rejects_type_conflict():
    registry = MetricRegistry()
    registry.inc_counter("calls_total")
    with pytest.raises(ValueError):
        registry.set_gauge("calls_total", 1)
    with pytest.raises(ValueError):
        registry.set_counter("calls_total", 1)


def test_registry_label_keys_must_match_definition():
    registry = MetricRegistry()
    for labels in ({"a": "1"}, {"a": "1", "b": "2", "c": "3"}):
        with pytest.raises(ValueError):
            registry.inc_counter("calls_total", labels=labels, label_names=("a", "b"))
    with pytest.raises(ValueError, match="bucket_schema is reserved"):
        registry.observe_histogram(
            "latency", 1, labels={"bucket_schema": "custom"}, label_names=("bucket_schema",)
        )


def test_registry_canonicalizes_label_name_order_for_same_family(render_prometheus):
    registry = MetricRegistry()
    for names in (("status", "provider"), ("provider", "status")):
        registry.inc_counter(
            "calls_total", labels={"status": "ok", "provider": "local"}, label_names=names
        )
    assert 'calls_total{provider="local",status="ok"} 2.0' in render_prometheus(registry)


def test_counter_only_increases():
    registry = MetricRegistry()
    registry.inc_counter("calls_total")
    for amount in (0, -1, float("inf")):
        with pytest.raises(ValueError):
            registry.inc_counter("calls_total", amount=amount)


def test_histogram_boundary_bucket(registry, render_prometheus):
    durations = (0.05, 20.0, 60.0, 60.001, 120.0, 3600.0, 404261.58497)
    for duration in durations:
        registry.observe_histogram("latency_seconds", duration)
    family = next(text_string_to_metric_families(render_prometheus(registry)))
    buckets = {float(s.labels["le"]): s.value for s in family.samples if s.name.endswith("_bucket")}
    for bound in DEFAULT_LATENCY_BUCKETS:
        assert buckets[bound] == sum(value <= bound for value in durations)
    assert buckets[float("inf")] == len(durations)
    schema = family.samples[0].labels["bucket_schema"]
    request = encode_metrics(registry.collect())
    point = request.resource_metrics[0].scope_metrics[0].metrics[0].histogram.data_points[0]
    assert tuple(point.explicit_bounds) == DEFAULT_LATENCY_BUCKETS
    assert point.count == sum(point.bucket_counts) == len(durations)
    assert point.bucket_counts[-1] == 0
    assert point.sum == pytest.approx(sum(durations))
    assert {a.key: a.value.string_value for a in point.attributes} == {"bucket_schema": schema}
    legacy = MetricRegistry()
    legacy.observe_histogram("latency_seconds", 60, buckets=(0.05, 0.1, 10.0))
    legacy_family = next(text_string_to_metric_families(render_prometheus(legacy)))
    assert legacy_family.samples[0].labels["bucket_schema"] != schema


def test_registry_absolute_values_and_deletion(render_prometheus):
    registry = MetricRegistry(max_series_per_metric=1)
    labels = {"plugin": "memfs"}
    started = None
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
        request = encode_metrics(registry.collect())
        metrics = {m.name: m for m in request.resource_metrics[0].scope_metrics[0].metrics}
        counter = metrics["calls_total"].sum.data_points[0]
        histogram = metrics["latency_seconds"].histogram.data_points[0]
        assert counter.as_double == histogram.count == value
        assert histogram.sum == pytest.approx(value * 0.001)
        assert not histogram.HasField("min") and not histogram.HasField("max")
        if value == 10:
            started = counter.start_time_unix_nano
        elif value == 12:
            assert counter.start_time_unix_nano == started
        else:
            assert counter.start_time_unix_nano > started
        assert histogram.start_time_unix_nano <= histogram.time_unix_nano
    registry.set_counter("calls_total", 5, labels={"plugin": "other"}, label_names=("plugin",))
    text = render_prometheus(registry)
    assert 'plugin="other"' not in text
    assert 'openviking_metrics_dropped_series_total{metric="calls_total"} 1.0' in text
    registry.delete_matching("calls_total", match_labels=labels)
    registry.delete_matching("latency_seconds", match_labels=labels)
    assert not any(
        line.startswith(("calls_total", "latency_seconds"))
        for line in render_prometheus(registry).splitlines()
    )


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_registry_rejects_invalid_absolute_counter(value, render_prometheus):
    registry = MetricRegistry()
    registry.set_counter("calls_total", 4)
    with pytest.raises(ValueError):
        registry.set_counter("calls_total", value)
    assert "calls_total 4.0" in render_prometheus(registry)


@pytest.mark.parametrize("kind", ("counter", "gauge", "histogram"))
def test_registry_partial_deletion_keeps_active_series(kind, render_prometheus):
    registry = MetricRegistry()
    name = "active_total" if kind == "counter" else "active"
    for plugin in ("removed", "kept"):
        labels = {"plugin": plugin}
        if kind == "histogram":
            registry.set_histogram(
                name,
                bucket_bounds=[1],
                bucket_counts=[1, 0],
                count=1,
                value_sum=0.5,
                labels=labels,
                label_names=("plugin",),
            )
        else:
            getattr(registry, f"set_{kind}")(name, 1, labels=labels, label_names=("plugin",))
    delete = registry.delete_matching
    delete(name, match_labels={"plugin": "removed"})
    text = render_prometheus(registry)
    assert 'plugin="removed"' not in text and 'plugin="kept"' in text
    delete(name, match_labels={"plugin": "kept"})
    assert name not in render_prometheus(registry)
    with pytest.raises(ValueError):
        registry.set_gauge(name, 2, labels={"other": "value"}, label_names=("other",))
