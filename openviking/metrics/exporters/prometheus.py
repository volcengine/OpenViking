# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Prometheus exporter implementation.

Key behaviors:
- On each export, attempts to refresh all registered collectors (best-effort).
- Renders the current registry state into Prometheus text exposition format.

The exporter is intentionally tolerant:
- Collector refresh errors are swallowed (metrics must not break `/metrics`).
- Families with no samples are still exported as a single zero sample when possible,
  to keep dashboards stable.
"""

from __future__ import annotations

from typing import Iterable

from openviking.metrics.core.base import MetricExporter
from openviking.metrics.core.types import render_labels


class PrometheusExporter(MetricExporter):
    """
    Render the in-process registry into Prometheus text exposition format.

    This exporter optionally refreshes refresh-managed collectors before rendering, then walks
    the registry families and serializes counters, gauges, histograms, and dropped-series
    diagnostics into a single text payload suitable for `/metrics`.
    """

    def __init__(
        self, *, registry, collector_manager=None, refresh_deadline_seconds: float = 1.0
    ) -> None:
        """
        Initialize the Prometheus exporter.

        Args:
            registry: The in-process metric registry that stores current metric values.
            collector_manager: Optional refresh orchestrator invoked before each export.
            refresh_deadline_seconds: Best-effort time budget for the pre-render refresh stage.
        """
        self._registry = registry
        self._collector_manager = collector_manager
        self._refresh_deadline_seconds = float(refresh_deadline_seconds)

    async def export(self) -> str:
        """
        Refresh collectors if configured and return the Prometheus exposition payload.

        Collector refresh remains best-effort: refresh failures are swallowed so observability
        does not break the serving of the metrics endpoint itself.
        """
        if self._collector_manager is not None:
            try:
                await self._collector_manager.refresh_all(
                    self._registry,
                    deadline_seconds=self._refresh_deadline_seconds,
                )
            except Exception:
                pass
        return self.render()

    def render(self) -> str:
        """
        Serialize the registry into Prometheus text exposition format.

        Returns:
            A newline-terminated text payload containing every registered family in the
            Prometheus exposition layout expected by scrapers and tooling.
        """
        lines: list[str] = []

        # Process counters
        for name, counter in self._registry.iter_counters():
            self._add_help_and_type(lines, name, "counter")
            self._process_counter_series(lines, name, counter)

        # Process gauges
        for name, gauge in self._registry.iter_gauges():
            self._add_help_and_type(lines, name, "gauge")
            self._process_gauge_series(lines, name, gauge)

        # Process histograms
        for name, label_names, bucket_bounds, series_iter in self._registry.iter_histograms():
            self._add_help_and_type(lines, name, "histogram")
            series_list = list(series_iter)
            if not series_list and not label_names:
                self._render_empty_histogram(lines, name, bucket_bounds)
            else:
                for labels, bucket_counts, count, value_sum in series_list:
                    self._render_histogram_series(
                        lines, name, bucket_bounds, labels, bucket_counts, count, value_sum
                    )

        # Process dropped series
        for metric_name, dropped in self._registry.iter_dropped_series():
            lines.append(
                f'openviking_metrics_dropped_series_total{{metric="{metric_name}"}} {dropped}'
            )

        return "\n".join(lines) + "\n"

    def _add_help_and_type(self, lines: list[str], name: str, metric_type: str) -> None:
        """Append the Prometheus `HELP` and `TYPE` prelude for one metric family."""
        lines.append(f"# HELP {name} OpenViking metric.")
        lines.append(f"# TYPE {name} {metric_type}")

    def _process_counter_series(self, lines: list[str], name: str, counter) -> None:
        """
        Render all counter series for one family into the output buffer.

        Empty unlabeled counters are rendered as a single zero sample to keep dashboards and
        alert queries stable before the first real increment occurs.
        """
        if not counter and not self._registry.counter_label_names(name):
            lines.append(f"{name} 0")
        for labels, value in counter:
            lines.append(
                f"{name}{render_labels(labels)} {int(value) if value.is_integer() else value}"
            )

    def _process_gauge_series(self, lines: list[str], name: str, gauge) -> None:
        """
        Render all gauge series for one family into the output buffer.

        Empty unlabeled gauges also emit one zero sample so consumers do not need to special-case
        missing series before initialization.
        """
        if not gauge and not self._registry.gauge_label_names(name):
            lines.append(f"{name} 0")
        for labels, value in gauge:
            lines.append(f"{name}{render_labels(labels)} {value}")

    def _render_empty_histogram(
        self, lines: list[str], name: str, bucket_bounds: tuple[float, ...]
    ) -> None:
        """Render an unlabeled histogram family with zero samples across every bucket."""
        zero_counts = (0,) * (len(bucket_bounds) + 1)
        cumulative = 0
        for bound, c in _iter_histogram_buckets(bucket_bounds, zero_counts):
            cumulative += c
            lines.append(f"{name}_bucket{render_labels((('le', bound),))} {cumulative}")
        lines.append(f"{name}_count 0")
        lines.append(f"{name}_sum 0")

    def _render_histogram_series(
        self,
        lines: list[str],
        name: str,
        bucket_bounds: tuple[float, ...],
        labels: tuple[tuple[str, str], ...],
        bucket_counts: tuple[int, ...],
        count: int,
        value_sum: float,
    ) -> None:
        """
        Render one histogram series with stable label ordering and cumulative bucket counts.

        Histogram buckets are exported cumulatively per the Prometheus exposition rules, while
        `_count` and `_sum` reuse the same sorted label tuple for deterministic output.
        """
        # Sort labels once per series to avoid repeated sorting
        sorted_labels = sorted(labels, key=lambda x: x[0])
        cumulative = 0
        for bound, c in _iter_histogram_buckets(bucket_bounds, bucket_counts):
            cumulative += c
            # Insert 'le' label in correct position without full sort
            merged_labels = self._merge_le_label(sorted_labels, str(bound))
            lines.append(f"{name}_bucket{render_labels(merged_labels)} {cumulative}")
        # Use sorted labels for count and sum to ensure consistent order
        lines.append(f"{name}_count{render_labels(tuple(sorted_labels))} {count}")
        lines.append(f"{name}_sum{render_labels(tuple(sorted_labels))} {value_sum}")

    def _merge_le_label(
        self, sorted_labels: list[tuple[str, str]], le: str
    ) -> tuple[tuple[str, str], ...]:
        """Insert the histogram `le` label into an already sorted label list without resorting."""
        # Find insertion point for 'le' label
        pos = len(sorted_labels)
        for i, (key, _) in enumerate(sorted_labels):
            if key > "le":
                pos = i
                break
        # Create new list with 'le' inserted
        merged = sorted_labels.copy()
        merged.insert(pos, ("le", le))
        return tuple(merged)


def _iter_histogram_buckets(
    bounds: tuple[float, ...], counts: tuple[int, ...]
) -> Iterable[tuple[str, int]]:
    """
    Yield histogram bucket upper bounds paired with their per-bucket sample counts.

    The final synthetic `+Inf` bucket is always yielded after the configured finite bounds.
    """
    for i, upper in enumerate(bounds):
        yield str(upper), counts[i]
    yield "+Inf", counts[len(bounds)]
