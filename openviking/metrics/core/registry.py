# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Bind OpenViking metric names and tags to OpenTelemetry SDK instruments.

The SDK owns event aggregation. Already accumulated native/state values are kept
as SDK data points and exported as snapshots, never replayed as observations.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Mapping

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    Gauge,
    Histogram,
    HistogramDataPoint,
    InMemoryMetricReader,
    Metric,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
    Sum,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope

from .types import histogram_schema, normalize_labels

# SDK collection policy: a compact 1/2/5 scale from milliseconds to multi-day work.
# Business collectors record actual seconds and do not manage these boundaries.
DEFAULT_LATENCY_BUCKETS = tuple(
    factor * 10.0**power for power in range(-2, 7) for factor in (1, 2, 5)
)


@dataclass
class _MetricBinding:
    kind: str
    label_names: tuple[str, ...]
    mode: str
    bounds: tuple[float, ...] = ()
    instrument: object | None = None
    points: dict[tuple[tuple[str, str], ...], NumberDataPoint | HistogramDataPoint | None] = field(
        default_factory=dict
    )


class MetricRegistry:
    """Keep metric contracts and adapt state snapshots; delegate accumulation to the SDK."""

    def __init__(self, *, max_series_per_metric: int = 2048) -> None:
        self._lock = threading.RLock()
        self._max_series = max_series_per_metric
        self._bindings: dict[str, _MetricBinding] = {}
        self._reader = InMemoryMetricReader()
        self._provider = MeterProvider(
            metric_readers=[self._reader], resource=Resource({}), shutdown_on_exit=False
        )
        self._meter = self._provider.get_meter("openviking.metrics")
        self._dropped = self._meter.create_counter("openviking_metrics_dropped_series_total")

    def _bind(self, name, kind, label_names, labels, *, mode, bounds=()):
        names = tuple(sorted(str(key) for key in label_names))
        normalized = normalize_labels(labels)
        if tuple(key for key, _ in normalized) != names:
            raise ValueError(f"metric {name} label keys mismatch: expected {names}")
        if kind == "histogram" and "bucket_schema" in names:
            raise ValueError("bucket_schema is reserved for the histogram's actual bucket layout")
        binding = self._bindings.get(name)
        if binding is None:
            instrument = None
            if mode == "event":
                if kind == "counter":
                    instrument = self._meter.create_counter(name)
                else:
                    instrument = self._meter.create_histogram(
                        name, explicit_bucket_boundaries_advisory=bounds
                    )
            binding = _MetricBinding(kind, names, mode, bounds, instrument)
            self._bindings[name] = binding
        elif (binding.kind, binding.label_names, binding.mode, binding.bounds) != (
            kind,
            names,
            mode,
            bounds,
        ):
            raise ValueError(f"metric {name} type, labels, collection mode or buckets mismatch")
        if normalized not in binding.points and len(binding.points) >= self._max_series:
            self._dropped.add(1, {"metric": name})
            return None, normalized
        return binding, normalized

    def inc_counter(self, name, *, amount=1.0, labels=None, label_names=()) -> None:
        amount = float(amount)
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("counter increment must be finite and positive")
        with self._lock:
            binding, key = self._bind(name, "counter", label_names, labels, mode="event")
            if binding is not None:
                binding.instrument.add(amount, dict(key))
                binding.points[key] = None

    def observe_histogram(
        self, name, value, *, labels=None, label_names=(), buckets=DEFAULT_LATENCY_BUCKETS
    ) -> None:
        value = float(value)
        bounds = self._bounds(buckets)
        if not math.isfinite(value) or value < 0:
            raise ValueError("histogram observation must be finite and nonnegative")
        with self._lock:
            binding, key = self._bind(
                name, "histogram", label_names, labels, mode="event", bounds=bounds
            )
            if binding is not None:
                binding.instrument.record(value, dict(key))
                binding.points[key] = None

    @staticmethod
    def _bounds(buckets):
        bounds = tuple(float(value) for value in buckets)
        if any(not math.isfinite(value) or value < 0 for value in bounds) or any(
            left >= right for left, right in zip(bounds, bounds[1:], strict=False)
        ):
            raise ValueError("histogram bounds must be finite, nonnegative and increasing")
        return bounds

    def set_counter(self, name, value, *, labels=None, label_names=()) -> None:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("counter value must be finite and nonnegative")
        self._set_number(name, value, "counter", labels, label_names)

    def set_gauge(self, name, value, *, labels=None, label_names=()) -> None:
        self._set_number(name, float(value), "gauge", labels, label_names)

    def _set_number(self, name, value, kind, labels, label_names):
        with self._lock:
            binding, key = self._bind(name, kind, label_names, labels, mode="snapshot")
            if binding is None:
                return
            now = time.time_ns()
            previous = binding.points.get(key)
            start = now
            if previous is not None and (kind == "gauge" or value >= previous.value):
                start = previous.start_time_unix_nano
            binding.points[key] = NumberDataPoint(dict(key), start, now, value)

    def set_histogram(
        self, name, *, bucket_bounds, bucket_counts, count, value_sum, labels=None, label_names=()
    ) -> None:
        """Accept a native histogram validated at the RagfsMetricDataSource boundary."""
        bounds = tuple(bucket_bounds)
        counts = tuple(bucket_counts)
        with self._lock:
            binding, key = self._bind(
                name, "histogram", label_names, labels, mode="snapshot", bounds=bounds
            )
            if binding is None:
                return
            now = time.time_ns()
            previous = binding.points.get(key)
            start = now
            if (
                previous is not None
                and count >= previous.count
                and value_sum >= previous.sum
                and all(
                    value >= old for value, old in zip(counts, previous.bucket_counts, strict=True)
                )
            ):
                start = previous.start_time_unix_nano
            # Native snapshots do not contain min/max; None omits these optional OTLP fields.
            binding.points[key] = HistogramDataPoint(
                attributes=dict(key),
                start_time_unix_nano=start,
                time_unix_nano=now,
                count=count,
                sum=value_sum,
                bucket_counts=counts,
                explicit_bounds=bounds,
                min=None,
                max=None,
            )

    def gauge_get(self, name, *, labels=None):
        with self._lock:
            binding = self._bindings.get(name)
            if binding is None or binding.kind != "gauge":
                return None
            point = binding.points.get(normalize_labels(labels))
            return None if point is None else point.value

    def delete_matching(self, name, *, match_labels: Mapping[str, str]) -> None:
        """Remove matching state snapshots; the registered metric owns its type."""
        match = set(normalize_labels(match_labels))
        with self._lock:
            binding = self._bindings.get(name)
            if binding is None:
                return
            if binding.mode != "snapshot":
                raise ValueError(f"metric {name} is not a state snapshot")
            for key in list(binding.points):
                if match.issubset(key):
                    del binding.points[key]

    def collect(self, *, service_name="openviking-server") -> MetricsData:
        """Return SDK event aggregates and current native/state snapshots in one standard batch."""
        with self._lock:
            data = self._reader.get_metrics_data()
            metrics = [
                metric
                for resource in (() if data is None else data.resource_metrics)
                for scope in resource.scope_metrics
                for metric in scope.metrics
            ]
            now = time.time_ns()
            for name, binding in self._bindings.items():
                if binding.mode != "snapshot" or not binding.points:
                    continue
                points = tuple(
                    replace(point, time_unix_nano=now) for point in binding.points.values()
                )
                if binding.kind == "counter":
                    aggregate = Sum(points, AggregationTemporality.CUMULATIVE, True)
                elif binding.kind == "gauge":
                    aggregate = Gauge(points)
                else:
                    aggregate = Histogram(points, AggregationTemporality.CUMULATIVE)
                metrics.append(Metric(name, "OpenViking metric.", "", aggregate))
        # Use the actual SDK boundaries, including boundaries from the Rust SDK.
        for index, metric in enumerate(metrics):
            if isinstance(metric.data, Histogram) and metric.data.data_points:
                schema = histogram_schema(tuple(metric.data.data_points[0].explicit_bounds))
                points = tuple(
                    replace(
                        point,
                        attributes={
                            **point.attributes,
                            "bucket_schema": schema,
                        },
                    )
                    for point in metric.data.data_points
                )
                metrics[index] = replace(metric, data=replace(metric.data, data_points=points))
        return MetricsData(
            (
                ResourceMetrics(
                    resource=Resource({"service.name": service_name}),
                    scope_metrics=(
                        ScopeMetrics(
                            scope=InstrumentationScope("openviking.metrics"),
                            metrics=tuple(sorted(metrics, key=lambda metric: metric.name)),
                            schema_url="",
                        ),
                    ),
                    schema_url="",
                ),
            )
        )
