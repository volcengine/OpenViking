# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
DomainStats collector: ObserverStateCollector.

This collector converts the observer's component status table into a small set of
low-cardinality gauges, suitable for dashboards and alerting.

Why not export the full debug status table?
- The debug output is verbose and not stable as a Prometheus label/value.
- Prometheus works best with bounded series counts and stable labels.

Failure semantics:
- On refresh success: export values with `valid="1"`.
- On refresh failure: export the last successful values with `valid="0"` (SWR-style),
  so `/metrics` stays available and alerting can use `valid` to detect staleness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.observer_state import ObserverStateDataSource

from .base import CollectorConfig, DomainStatsMetricCollector


@dataclass
class ObserverStateCollector(DomainStatsMetricCollector):
    """
    Summarize observer component states into three low-cardinality gauges.

    This collector intentionally collapses the detailed component table into global counts so
    high-level dashboards can alert on observer degradation without depending on per-component
    labels. A `valid` label distinguishes fresh snapshots from stale fallback values.
    """

    DOMAIN: ClassVar[str] = "observer"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_components_total
    # e.g.: openviking_observer_components_total
    COMPONENTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "components", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_components_unhealthy
    # e.g.: openviking_observer_components_unhealthy
    COMPONENTS_UNHEALTHY: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "components_unhealthy"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_components_with_errors
    # e.g.: openviking_observer_components_with_errors
    COMPONENTS_WITH_ERRORS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "components_with_errors"
    )

    data_source: ObserverStateDataSource
    config: CollectorConfig = CollectorConfig(ttl_seconds=10.0, timeout_seconds=0.8)
    _last_total: float = field(default=0.0, init=False, repr=False)
    _last_unhealthy: float = field(default=0.0, init=False, repr=False)
    _last_with_errors: float = field(default=0.0, init=False, repr=False)

    def read_metric_input(self):
        """Read the latest observer component-state mapping from the datasource."""
        return self.data_source.read_component_states()

    def collect_hook(self, registry, metric_input) -> None:
        """Refresh observer summary gauges using a successfully-read component state mapping."""
        states = metric_input
        total = 0.0
        unhealthy = 0.0
        with_errors = 0.0
        for _, state in states.items():
            if state is None:
                continue
            total += 1.0
            if not bool(getattr(state, "is_healthy", False)):
                unhealthy += 1.0
            if bool(getattr(state, "has_errors", False)):
                with_errors += 1.0

        self._last_total = total
        self._last_unhealthy = unhealthy
        self._last_with_errors = with_errors
        self._write(registry, valid="1", total=total, unhealthy=unhealthy, with_errors=with_errors)

    def collect_error_hook(self, registry, error: Exception) -> None:
        """Re-publish the last observer summary under `valid=0` when refresh fails."""
        self._write(
            registry,
            valid="0",
            total=self._last_total,
            unhealthy=self._last_unhealthy,
            with_errors=self._last_with_errors,
        )

    def _write(
        self, registry, *, valid: str, total: float, unhealthy: float, with_errors: float
    ) -> None:
        """Replace the observer summary families with one consistent validity-labelled snapshot."""
        self.replace_global_gauges(
            registry,
            metric_names=[
                self.COMPONENTS_TOTAL,
                self.COMPONENTS_UNHEALTHY,
                self.COMPONENTS_WITH_ERRORS,
            ],
            values=[total, unhealthy, with_errors],
            labels={"valid": str(valid)},
            label_names=("valid",),
        )
