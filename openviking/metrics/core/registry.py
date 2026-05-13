# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
In-process metric registry used by OpenViking.

Design goals:
1) Keep the registry as a "current state store" for metrics (no snapshot/view layer).
2) Provide a strict label contract per metric name to prevent accidental high-cardinality exports.
3) Be safe under concurrent updates from request threads and exporter refresh threads.
4) Allow bounded memory usage via per-metric series limits, while exposing drop counts for debugging.

Important behaviors:
- Label normalization: label dicts are normalized into a sorted tuple of (key, value) pairs.
- Label contract: once a metric name is registered with a label key set, subsequent writes must
  use the same label keys (same keys, same order). Violations raise ValueError.
- Series limit: when a metric family reaches `max_series_per_metric`, new series are dropped
  and `openviking_metrics_dropped_series_total{metric="<name>"}` will reflect the drops.
"""

from __future__ import annotations

import threading
from bisect import bisect_left
from typing import Mapping, Sequence

from .types import normalize_labels

DEFAULT_LATENCY_BUCKETS: tuple[float, ...] = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _canonicalize_label_names(label_names: Sequence[str]) -> tuple[str, ...]:
    """
    Return the canonical family label order used by the in-process registry.

    The registry normalizes concrete label mappings into key-sorted tuples, so family-level
    `label_names` must follow the same stable order to avoid false mismatches between writers
    that provide the same key set with different input ordering.
    """
    return tuple(sorted(str(name) for name in label_names))


def _labels_contains(
    labels: tuple[tuple[str, str], ...],
    match: tuple[tuple[str, str], ...],
) -> bool:
    """
    Return whether a normalized label set contains all requested label pairs.

    This helper is used by partial-delete operations where callers provide only a subset
    of labels and expect every matching series to be removed.
    """
    if not match:
        return True
    label_set = set(labels)
    for item in match:
        if item not in label_set:
            return False
    return True


class MetricRegistry:
    """
    MetricRegistry holds Counter/Gauge/Histogram families in memory.

    The exporter reads current values via `iter_counters/iter_gauges/iter_histograms`.
    Collectors should use the unified write helpers (`inc_counter/set_gauge/observe_histogram`)
    to keep call sites consistent and reduce accidental label misuse.
    """

    def __init__(self, *, max_series_per_metric: int = 2048) -> None:
        """
        Initialize an empty in-process metrics registry.

        Args:
            max_series_per_metric: Hard cap for the number of distinct label series allowed
                inside one metric family before additional series are dropped.
        """
        self._max_series_per_metric = max_series_per_metric
        self._lock = threading.Lock()
        self._counters: dict[str, _CounterFamily] = {}
        self._gauges: dict[str, _GaugeFamily] = {}
        self._histograms: dict[str, _HistogramFamily] = {}
        self._dropped_series_total: dict[str, int] = {}

    def inc_counter(
        self,
        name: str,
        *,
        amount: float = 1.0,
        labels: Mapping[str, str] | None = None,
        label_names: Sequence[str] = (),
    ) -> None:
        """
        Increment a Counter metric.

        Args:
            name: Prometheus metric name.
            amount: Increment delta (float, will be rendered as int when possible).
            labels: Optional label dict. Keys must exactly match `label_names`.
            label_names: Ordered label key tuple for this metric name.

        Notes:
            This helper enforces the per-metric label contract by delegating to the underlying
            Counter family, which validates label keys.
        """
        self.counter(name, label_names=label_names).inc(labels=labels, amount=float(amount))

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
        label_names: Sequence[str] = (),
    ) -> None:
        """
        Set a Gauge metric to an absolute value.

        Args:
            name: Prometheus metric name.
            value: Gauge value (float).
            labels: Optional label dict. Keys must exactly match `label_names`.
            label_names: Ordered label key tuple for this metric name.
        """
        self.gauge(name, label_names=label_names).set(float(value), labels=labels)

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
        label_names: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_LATENCY_BUCKETS,
    ) -> None:
        """
        Observe a value into a Histogram metric.

        Args:
            name: Prometheus metric name (base name; exporter renders _bucket/_count/_sum).
            value: Observation value.
            labels: Optional label dict. Keys must exactly match `label_names`.
            label_names: Ordered label key tuple for this metric name.
            buckets: Histogram bucket boundaries (ascending). "+Inf" bucket is implicit.
        """
        self.histogram(name, label_names=label_names, buckets=buckets).observe(
            float(value), labels=labels
        )

    def counter(self, name: str, *, label_names: Sequence[str] = ()) -> "_Counter":
        """
        Get or create a Counter family handle.

        Args:
            name: Prometheus metric name.
            label_names: Canonical ordered label keys for the family.

        Returns:
            A lightweight `_Counter` wrapper bound to the requested family.
        """
        ln = _canonicalize_label_names(label_names)
        with self._lock:
            if name in self._gauges or name in self._histograms:
                raise ValueError(f"metric {name} already registered with a different type")
            family = self._counters.get(name)
            if family is None:
                family = _CounterFamily(
                    name=name,
                    label_names=ln,
                    max_series=self._max_series_per_metric,
                    on_drop=self._on_drop_series,
                )
                self._counters[name] = family
            else:
                family._validate_label_names(ln)
        return _Counter(family)

    def gauge(self, name: str, *, label_names: Sequence[str] = ()) -> "_Gauge":
        """
        Get or create a Gauge family handle.

        Args:
            name: Prometheus metric name.
            label_names: Canonical ordered label keys for the family.

        Returns:
            A lightweight `_Gauge` wrapper bound to the requested family.
        """
        ln = _canonicalize_label_names(label_names)
        with self._lock:
            if name in self._counters or name in self._histograms:
                raise ValueError(f"metric {name} already registered with a different type")
            family = self._gauges.get(name)
            if family is None:
                family = _GaugeFamily(
                    name=name,
                    label_names=ln,
                    max_series=self._max_series_per_metric,
                    on_drop=self._on_drop_series,
                )
                self._gauges[name] = family
            else:
                family._validate_label_names(ln)
        return _Gauge(family)

    def histogram(
        self,
        name: str,
        *,
        label_names: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_LATENCY_BUCKETS,
    ) -> "_Histogram":
        """
        Get or create a Histogram family handle.

        Args:
            name: Prometheus metric base name.
            label_names: Canonical ordered label keys for the family.
            buckets: Histogram bucket upper bounds.

        Returns:
            A lightweight `_Histogram` wrapper bound to the requested family.
        """
        ln = _canonicalize_label_names(label_names)
        b = tuple(float(x) for x in buckets)
        with self._lock:
            if name in self._counters or name in self._gauges:
                raise ValueError(f"metric {name} already registered with a different type")
            family = self._histograms.get(name)
            if family is None:
                family = _HistogramFamily(
                    name=name,
                    label_names=ln,
                    bucket_bounds=b,
                    max_series=self._max_series_per_metric,
                    on_drop=self._on_drop_series,
                )
                self._histograms[name] = family
            else:
                family._validate_label_names(ln)
                family._validate_buckets(b)
        return _Histogram(family)

    def iter_counters(self):
        """
        Iterate over all counter families and their current series values.

        The returned family payload is a detached snapshot so exporters can iterate without
        holding the registry lock during rendering.
        """
        with self._lock:
            families = dict(self._counters)
        for name, family in families.items():
            yield name, list(family.copy_values().items())

    def counter_label_names(self, name: str) -> tuple[str, ...]:
        """Return the registered label key tuple for a counter family, if present."""
        with self._lock:
            family = self._counters.get(name)
            return family.label_names if family is not None else ()

    def iter_gauges(self):
        """
        Iterate over all gauge families and their current series values.

        As with counters, the family payload is copied before iteration so rendering stays
        independent from concurrent writers.
        """
        with self._lock:
            families = dict(self._gauges)
        for name, family in families.items():
            yield name, list(family.copy_values().items())

    def gauge_label_names(self, name: str) -> tuple[str, ...]:
        """Return the registered label key tuple for a gauge family, if present."""
        with self._lock:
            family = self._gauges.get(name)
            return family.label_names if family is not None else ()

    def gauge_get(self, name: str, *, labels: Mapping[str, str] | None = None) -> float | None:
        """
        Read the current value of a gauge series.

        Args:
            name: Gauge metric name.
            labels: Optional label dict identifying a single series.

        Returns:
            The current gauge value, or `None` when the family or series does not exist.
        """
        with self._lock:
            family = self._gauges.get(name)
        if family is None:
            return None
        return family.get_value(labels=labels)

    def gauge_delete_matching(self, name: str, *, match_labels: Mapping[str, str]) -> None:
        """
        Delete every gauge series whose labels contain the provided subset.

        Args:
            name: Gauge metric name.
            match_labels: Partial label set used for matching series to remove.
        """
        with self._lock:
            family = self._gauges.get(name)
        if family is None:
            return
        family.delete_matching(match_labels=match_labels)

    def iter_histograms(self):
        """
        Iterate over all histogram families and their materialized series snapshots.

        Each yielded item contains family metadata and a detached list of per-series bucket
        counts, sample counts, and sums ready for exporter rendering.
        """
        with self._lock:
            families = dict(self._histograms)
        for name, family in families.items():
            label_names, bucket_bounds, series = family.copy_series()
            yield name, label_names, bucket_bounds, series

    def iter_dropped_series(self):
        """
        Iterate over metrics that have dropped series because of series-limit enforcement.

        Exporters use this bookkeeping to expose internal pressure signals when a metric family
        hits its cardinality cap and starts rejecting additional series.
        """
        with self._lock:
            for metric_name, dropped in self._dropped_series_total.items():
                yield metric_name, dropped

    def _on_drop_series(self, metric_name: str) -> None:
        """Record that a new series was rejected for the given metric family."""
        with self._lock:
            self._dropped_series_total[metric_name] = (
                self._dropped_series_total.get(metric_name, 0) + 1
            )


class _CounterFamily:
    """Internal mutable storage for one counter metric family and all of its series."""

    def __init__(
        self,
        *,
        name: str,
        label_names: tuple[str, ...],
        max_series: int,
        on_drop,
    ) -> None:
        """Create an internal counter family with bounded series cardinality."""
        self.name = name
        self.label_names = label_names
        self._max_series = max_series
        self._on_drop = on_drop
        self._lock = threading.Lock()
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def inc(self, *, labels: Mapping[str, str] | None, amount: float) -> None:
        """
        Increase one counter series by a positive amount.

        New series are rejected once the family has reached the configured series limit, but
        existing series may continue to grow even after the cap is reached.
        """
        if amount <= 0:
            raise ValueError("counter can only be increased by a positive amount")
        key = self._normalize_and_validate(labels)
        with self._lock:
            if key not in self._values and len(self._values) >= self._max_series:
                self._on_drop(self.name)
                return
            self._values[key] = self._values.get(key, 0.0) + float(amount)

    def copy_values(self) -> dict[tuple[tuple[str, str], ...], float]:
        """Return a detached copy of all series values in this family."""
        with self._lock:
            return dict(self._values)

    def _normalize_and_validate(
        self, labels: Mapping[str, str] | None
    ) -> tuple[tuple[str, str], ...]:
        """Normalize label input and enforce the family label contract."""
        normalized = normalize_labels(labels)
        self._validate_label_names_against(normalized)
        return normalized

    def _validate_label_names(self, label_names: tuple[str, ...]) -> None:
        """Ensure subsequent family lookups use the original declared label keys."""
        if self.label_names != label_names:
            raise ValueError(
                f"metric {self.name} label_names mismatch: {self.label_names} vs {label_names}"
            )

    def _validate_label_names_against(self, normalized: tuple[tuple[str, str], ...]) -> None:
        """Ensure a concrete series write uses exactly the configured label keys."""
        if not self.label_names and normalized:
            raise ValueError(f"metric {self.name} does not accept labels")
        if self.label_names and tuple(k for k, _ in normalized) != self.label_names:
            raise ValueError(f"metric {self.name} label keys mismatch: expected {self.label_names}")


class _GaugeFamily:
    """Internal mutable storage for one gauge metric family and all of its series."""

    def __init__(
        self,
        *,
        name: str,
        label_names: tuple[str, ...],
        max_series: int,
        on_drop,
    ) -> None:
        """Create an internal gauge family with bounded series cardinality."""
        self.name = name
        self.label_names = label_names
        self._max_series = max_series
        self._on_drop = on_drop
        self._lock = threading.Lock()
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def set(self, *, labels: Mapping[str, str] | None, value: float) -> None:
        """
        Set one gauge series to an absolute value.

        As with counters, creating a brand-new series is still subject to the family-level
        cardinality cap.
        """
        key = self._normalize_and_validate(labels)
        with self._lock:
            if key not in self._values and len(self._values) >= self._max_series:
                self._on_drop(self.name)
                return
            self._values[key] = float(value)

    def add(self, *, labels: Mapping[str, str] | None, delta: float) -> None:
        """
        Add a signed delta to one gauge series.

        This is the internal primitive behind the public gauge `inc(...)` and `dec(...)`
        helpers.
        """
        key = self._normalize_and_validate(labels)
        with self._lock:
            if key not in self._values and len(self._values) >= self._max_series:
                self._on_drop(self.name)
                return
            self._values[key] = self._values.get(key, 0.0) + float(delta)

    def copy_values(self) -> dict[tuple[tuple[str, str], ...], float]:
        """Return a detached copy of all gauge series values."""
        with self._lock:
            return dict(self._values)

    def get_value(self, *, labels: Mapping[str, str] | None) -> float | None:
        """Return the current value for one normalized gauge series, if that series exists."""
        key = self._normalize_and_validate(labels)
        with self._lock:
            return self._values.get(key)

    def delete_matching(self, *, match_labels: Mapping[str, str]) -> None:
        """Delete every stored gauge series that contains the requested label subset."""
        normalized = normalize_labels(match_labels)
        with self._lock:
            keys = list(self._values.keys())
            for k in keys:
                if _labels_contains(k, normalized):
                    self._values.pop(k, None)

    def _normalize_and_validate(
        self, labels: Mapping[str, str] | None
    ) -> tuple[tuple[str, str], ...]:
        """Normalize label input and enforce the family label contract."""
        normalized = normalize_labels(labels)
        self._validate_label_names_against(normalized)
        return normalized

    def _validate_label_names(self, label_names: tuple[str, ...]) -> None:
        """Ensure subsequent family lookups use the original declared label keys."""
        if self.label_names != label_names:
            raise ValueError(
                f"metric {self.name} label_names mismatch: {self.label_names} vs {label_names}"
            )

    def _validate_label_names_against(self, normalized: tuple[tuple[str, str], ...]) -> None:
        """Ensure a concrete series write uses exactly the configured label keys."""
        if not self.label_names and normalized:
            raise ValueError(f"metric {self.name} does not accept labels")
        if self.label_names and tuple(k for k, _ in normalized) != self.label_names:
            raise ValueError(f"metric {self.name} label keys mismatch: expected {self.label_names}")


class _HistogramFamily:
    """Internal mutable storage for one histogram metric family and all of its label series."""

    def __init__(
        self,
        *,
        name: str,
        label_names: tuple[str, ...],
        bucket_bounds: tuple[float, ...],
        max_series: int,
        on_drop,
    ) -> None:
        """Create an internal histogram family with fixed buckets and bounded series count."""
        self.name = name
        self.label_names = label_names
        self.bucket_bounds = bucket_bounds
        self._max_series = max_series
        self._on_drop = on_drop
        self._lock = threading.Lock()
        self._series: dict[tuple[tuple[str, str], ...], _HistogramSeries] = {}

    def observe(self, *, labels: Mapping[str, str] | None, value: float) -> None:
        """
        Record one histogram observation for the selected label series.

        A new series is materialized lazily on first use, subject to the same family-level
        series cap used by counters and gauges.
        """
        key = self._normalize_and_validate(labels)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                if len(self._series) >= self._max_series:
                    self._on_drop(self.name)
                    return
                series = _HistogramSeries(bucket_bounds=self.bucket_bounds)
                self._series[key] = series
            series.observe(float(value))

    def copy_series(
        self,
    ) -> tuple[
        tuple[str, ...],
        tuple[float, ...],
        list[tuple[tuple[tuple[str, str], ...], tuple[int, ...], int, float]],
    ]:
        """Return a detached snapshot of the histogram family and all of its series."""
        with self._lock:
            series_copy: dict[tuple[tuple[str, str], ...], _HistogramSeries] = dict(self._series)
            label_names = self.label_names
            bucket_bounds = self.bucket_bounds
        series: list[tuple[tuple[tuple[str, str], ...], tuple[int, ...], int, float]] = []
        for labels, s in series_copy.items():
            bucket_counts, count, value_sum = s.copy_values()
            series.append((labels, bucket_counts, count, value_sum))
        return label_names, bucket_bounds, series

    def _normalize_and_validate(
        self, labels: Mapping[str, str] | None
    ) -> tuple[tuple[str, str], ...]:
        """Normalize label input and enforce the family label contract."""
        normalized = normalize_labels(labels)
        self._validate_label_names_against(normalized)
        return normalized

    def _validate_label_names(self, label_names: tuple[str, ...]) -> None:
        """Ensure subsequent family lookups use the original declared label keys."""
        if self.label_names != label_names:
            raise ValueError(
                f"metric {self.name} label_names mismatch: {self.label_names} vs {label_names}"
            )

    def _validate_label_names_against(self, normalized: tuple[tuple[str, str], ...]) -> None:
        """Ensure a concrete series write uses exactly the configured label keys."""
        if not self.label_names and normalized:
            raise ValueError(f"metric {self.name} does not accept labels")
        if self.label_names and tuple(k for k, _ in normalized) != self.label_names:
            raise ValueError(f"metric {self.name} label keys mismatch: expected {self.label_names}")

    def _validate_buckets(self, bucket_bounds: tuple[float, ...]) -> None:
        """Ensure a histogram family is never reopened with a different bucket layout."""
        if self.bucket_bounds != bucket_bounds:
            raise ValueError(f"metric {self.name} buckets mismatch")


class _HistogramSeries:
    """Internal bucket/count/sum accumulator for one concrete histogram label series."""

    def __init__(self, *, bucket_bounds: tuple[float, ...]) -> None:
        """Create one histogram series with counters for all finite buckets plus `+Inf`."""
        self._bucket_bounds = bucket_bounds
        self._lock = threading.Lock()
        self._bucket_counts: list[int] = [0] * (len(bucket_bounds) + 1)
        self._count: int = 0
        self._sum: float = 0.0

    def observe(self, value: float) -> None:
        """
        Record a single observation into the appropriate bucket and aggregate summary totals.

        Bucket selection uses `bisect_left`, so each observation increments the first bucket
        whose upper bound is greater than or equal to the recorded value.
        """
        idx = bisect_left(self._bucket_bounds, value)
        with self._lock:
            self._bucket_counts[idx] += 1
            self._count += 1
            self._sum += value

    def copy_values(self) -> tuple[tuple[int, ...], int, float]:
        """Return immutable copies of bucket counts, sample count, and sum."""
        with self._lock:
            return tuple(self._bucket_counts), self._count, self._sum


class _Counter:
    """Public lightweight handle used by callers to mutate one counter family."""

    def __init__(self, family: _CounterFamily) -> None:
        """Bind a public counter handle to one internal counter family."""
        self._family = family

    def inc(self, amount: float = 1.0, *, labels: Mapping[str, str] | None = None) -> None:
        """Increment one series in the bound counter family using the public wrapper API."""
        self._family.inc(labels=labels, amount=amount)


class _Gauge:
    """Public lightweight handle used by callers to mutate one gauge family."""

    def __init__(self, family: _GaugeFamily) -> None:
        """Bind a public gauge handle to one internal gauge family."""
        self._family = family

    def set(self, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        """Set one series in the bound gauge family through the public wrapper API."""
        self._family.set(labels=labels, value=value)

    def inc(self, amount: float = 1.0, *, labels: Mapping[str, str] | None = None) -> None:
        """Increase one series in the bound gauge family by a positive delta."""
        self._family.add(labels=labels, delta=amount)

    def dec(self, amount: float = 1.0, *, labels: Mapping[str, str] | None = None) -> None:
        """Decrease one series in the bound gauge family by a positive amount."""
        self._family.add(labels=labels, delta=-amount)


class _Histogram:
    """Public lightweight handle used by callers to mutate one histogram family."""

    def __init__(self, family: _HistogramFamily) -> None:
        """Bind a public histogram handle to one internal histogram family."""
        self._family = family

    def observe(self, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        """Record one observation for a series in the bound histogram family wrapper."""
        self._family.observe(labels=labels, value=value)
