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

import math
import threading
import time
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


def _validate_label_keys(
    name: str,
    label_names: tuple[str, ...],
    normalized: tuple[tuple[str, str], ...],
) -> None:
    """Check normalized labels against the named metric's declared keys; return None."""
    if not label_names and normalized:
        raise ValueError(f"metric {name} does not accept labels")
    if label_names and tuple(k for k, _ in normalized) != label_names:
        raise ValueError(f"metric {name} label keys mismatch: expected {label_names}")


def _validate_counter_value(value: float) -> float:
    """Return value as a finite, nonnegative float or raise ValueError."""
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("counter value must be finite and nonnegative")
    return value


def _validate_histogram_values(
    bounds: tuple[float, ...],
    bucket_counts: Sequence[int],
    count: int,
    value_sum: float,
) -> tuple[tuple[int, ...], float]:
    """Validate bounds and totals; return bucket_counts as a tuple and value_sum as a float."""
    bucket_counts = tuple(bucket_counts)
    value_sum = float(value_sum)
    if any(not math.isfinite(bound) or bound < 0 for bound in bounds) or any(
        left >= right for left, right in zip(bounds, bounds[1:], strict=False)
    ):
        raise ValueError("histogram bounds must be finite, nonnegative and increasing")
    if len(bucket_counts) != len(bounds) + 1 or any(
        type(value) is not int or value < 0 for value in (*bucket_counts, count)
    ):
        raise ValueError("histogram counts must be nonnegative integers matching bounds")
    if sum(bucket_counts) != count or not math.isfinite(value_sum) or value_sum < 0:
        raise ValueError("histogram count or sum is invalid")
    return bucket_counts, value_sum


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

    def set_counter(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
        label_names: Sequence[str] = (),
    ) -> None:
        """Replace the named counter's labelled value, including zero or reset; return None."""
        value = _validate_counter_value(value)
        ln = _canonicalize_label_names(label_names)
        normalized = normalize_labels(labels)
        _validate_label_keys(name, ln, normalized)
        self.counter(name, label_names=ln).set(value, labels=dict(normalized))

    def set_histogram(
        self,
        name: str,
        *,
        bucket_bounds: Sequence[float],
        bucket_counts: Sequence[int],
        count: int,
        value_sum: float,
        labels: Mapping[str, str] | None = None,
        label_names: Sequence[str] = (),
    ) -> None:
        """Replace a named histogram from disjoint bucket counts and totals; return None."""
        bounds = tuple(float(bound) for bound in bucket_bounds)
        bucket_counts, value_sum = _validate_histogram_values(
            bounds, bucket_counts, count, value_sum
        )
        ln = _canonicalize_label_names(label_names)
        normalized = normalize_labels(labels)
        _validate_label_keys(name, ln, normalized)
        self.histogram(name, label_names=ln, buckets=bounds).set(
            bucket_counts, count, value_sum, labels=dict(normalized)
        )

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

    def iter_counters(self, *, include_start_time: bool = False):
        """
        Iterate over all counter families and their current series values.

        The returned family payload is a detached snapshot so exporters can iterate without
        holding the registry lock during rendering. When include_start_time is True,
        each series also includes its cumulative start time in Unix nanoseconds.
        """
        with self._lock:
            families = dict(self._counters)
        for name, family in families.items():
            values = family.copy_values()
            if values is not None:
                yield (
                    name,
                    [
                        (labels, *value) if include_start_time else (labels, value[0])
                        for labels, value in values.items()
                    ],
                )

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
            values = family.copy_values()
            if values is not None:
                yield name, list(values.items())

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

    def counter_delete_matching(self, name: str, *, match_labels: Mapping[str, str]) -> None:
        """Delete counter series matching the supplied label subset; return None."""
        with self._lock:
            family = self._counters.get(name)
        if family is not None:
            family.delete_matching(match_labels=match_labels)

    def histogram_delete_matching(self, name: str, *, match_labels: Mapping[str, str]) -> None:
        """Delete histogram series matching the supplied label subset; return None."""
        with self._lock:
            family = self._histograms.get(name)
        if family is not None:
            family.delete_matching(match_labels=match_labels)

    def iter_histograms(self, *, include_start_time: bool = False):
        """
        Iterate over all histogram families and their materialized series snapshots.

        Each yielded item contains family metadata and a detached list of per-series bucket
        counts, sample counts, and sums ready for exporter rendering. When include_start_time
        is True, each series also includes its cumulative start time in Unix nanoseconds.
        """
        with self._lock:
            families = dict(self._histograms)
        for name, family in families.items():
            snapshot = family.copy_series()
            if snapshot is not None:
                label_names, bucket_bounds, series = snapshot
                yield (
                    name,
                    label_names,
                    bucket_bounds,
                    (series if include_start_time else [values[:-1] for values in series]),
                )

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
        self._values: dict[tuple[tuple[str, str], ...], tuple[float, int]] = {}
        self._had_deletions = False

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
            value, start_time_ns = self._values.get(key, (0.0, 0))
            self._values[key] = (value + float(amount), start_time_ns or time.time_ns())

    def copy_values(self) -> dict[tuple[tuple[str, str], ...], tuple[float, int]] | None:
        """Return totals with their start times, or None if deletion emptied this family."""
        with self._lock:
            if self._had_deletions and not self._values:
                return None
            return dict(self._values)

    def set(self, *, labels: Mapping[str, str] | None, value: float) -> None:
        """Replace labels with value, starting a new cumulative period on decrease; return None."""
        value = _validate_counter_value(value)
        key = self._normalize_and_validate(labels)
        with self._lock:
            if key not in self._values and len(self._values) >= self._max_series:
                self._on_drop(self.name)
                return
            previous = self._values.get(key)
            start_time_ns = (
                previous[1] if previous is not None and value >= previous[0] else time.time_ns()
            )
            self._values[key] = (value, start_time_ns)

    def delete_matching(self, *, match_labels: Mapping[str, str]) -> None:
        """Remove counter entries containing the supplied labels; return None."""
        normalized = normalize_labels(match_labels)
        with self._lock:
            for key in list(self._values):
                if _labels_contains(key, normalized):
                    del self._values[key]
                    self._had_deletions = True

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
        _validate_label_keys(self.name, self.label_names, normalized)


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
        self._had_deletions = False

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

    def copy_values(self) -> dict[tuple[tuple[str, str], ...], float] | None:
        """Return a gauge value snapshot, or None if deletion emptied this family."""
        with self._lock:
            if self._had_deletions and not self._values:
                return None
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
                    self._had_deletions = True

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
        self._had_deletions = False

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

    def set(
        self,
        *,
        labels: Mapping[str, str] | None,
        bucket_counts: Sequence[int],
        count: int,
        value_sum: float,
    ) -> None:
        """Validate bucket_counts, count and value_sum before replacing labels; return None."""
        bucket_counts, value_sum = _validate_histogram_values(
            self.bucket_bounds, bucket_counts, count, value_sum
        )
        key = self._normalize_and_validate(labels)
        with self._lock:
            series = self._series.get(key)
            if series is None:
                if len(self._series) >= self._max_series:
                    self._on_drop(self.name)
                    return
                series = _HistogramSeries(bucket_bounds=self.bucket_bounds)
                self._series[key] = series
            series.set_values(bucket_counts, count, value_sum)

    def delete_matching(self, *, match_labels: Mapping[str, str]) -> None:
        """Remove histogram entries containing the supplied labels; return None."""
        normalized = normalize_labels(match_labels)
        with self._lock:
            for key in list(self._series):
                if _labels_contains(key, normalized):
                    del self._series[key]
                    self._had_deletions = True

    def copy_series(
        self,
    ) -> (
        tuple[
            tuple[str, ...],
            tuple[float, ...],
            list[tuple[tuple[tuple[str, str], ...], tuple[int, ...], int, float, int]],
        ]
        | None
    ):
        """Return histogram values and start times, or None if deletion emptied this family."""
        with self._lock:
            if self._had_deletions and not self._series:
                return None
            series: list[tuple[tuple[tuple[str, str], ...], tuple[int, ...], int, float, int]] = []
            for labels, s in self._series.items():
                series.append((labels, *s.copy_values()))
            return self.label_names, self.bucket_bounds, series

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
        _validate_label_keys(self.name, self.label_names, normalized)

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
        self._start_time_ns = time.time_ns()

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

    def copy_values(self) -> tuple[tuple[int, ...], int, float, int]:
        """Return bucket counts, sample count, sum and cumulative start time under one lock."""
        with self._lock:
            return tuple(self._bucket_counts), self._count, self._sum, self._start_time_ns

    def set_values(self, bucket_counts: tuple[int, ...], count: int, value_sum: float) -> None:
        """Replace validated totals, starting a new period if any total decreases; return None."""
        with self._lock:
            if (
                count < self._count
                or value_sum < self._sum
                or any(
                    current < previous
                    for current, previous in zip(bucket_counts, self._bucket_counts, strict=True)
                )
            ):
                self._start_time_ns = time.time_ns()
            self._bucket_counts = list(bucket_counts)
            self._count = count
            self._sum = value_sum


class _Counter:
    """Public lightweight handle used by callers to mutate one counter family."""

    def __init__(self, family: _CounterFamily) -> None:
        """Bind a public counter handle to one internal counter family."""
        self._family = family

    def inc(self, amount: float = 1.0, *, labels: Mapping[str, str] | None = None) -> None:
        """Increment one series in the bound counter family using the public wrapper API."""
        self._family.inc(labels=labels, amount=amount)

    def set(self, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        """Replace the labelled series with a validated absolute value; return None."""
        self._family.set(labels=labels, value=value)


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


class _Histogram:
    """Public lightweight handle used by callers to mutate one histogram family."""

    def __init__(self, family: _HistogramFamily) -> None:
        """Bind a public histogram handle to one internal histogram family."""
        self._family = family

    def observe(self, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        """Record one observation for a series in the bound histogram family wrapper."""
        self._family.observe(labels=labels, value=value)

    def set(
        self,
        bucket_counts: Sequence[int],
        count: int,
        value_sum: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Replace the labelled series using validated disjoint counts and totals; return None."""
        self._family.set(
            labels=labels, bucket_counts=bucket_counts, count=count, value_sum=value_sum
        )
