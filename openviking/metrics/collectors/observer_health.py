# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.observer_state import ObserverStateDataSource

from .base import CollectorConfig, StateMetricCollector


@dataclass
class ObserverHealthCollector(StateMetricCollector):
    """
    Export per-component observer health and error gauges with stale-on-error semantics.

    Unlike the summary-oriented observer collector, this class preserves one series per component
    so dashboards can pinpoint which subsystem degraded. Failed refreshes re-publish the last known
    component set with `valid="0"` instead of dropping the series entirely.
    """

    STALE_ON_ERROR: ClassVar[bool] = True

    DOMAIN: ClassVar[str] = "component"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_health
    # e.g.: openviking_component_health
    HEALTH: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "health")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_errors
    # e.g.: openviking_component_errors
    ERRORS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "errors")

    data_source: ObserverStateDataSource
    config: CollectorConfig = CollectorConfig(ttl_seconds=10.0, timeout_seconds=0.8)
    _known_components: list[str] = field(
        default_factory=lambda: ["queue", "models", "lock", "retrieval", "vikingdb"],
        init=False,
        repr=False,
    )

    def read_metric_input(self):
        """Read the latest observer component-state mapping from the datasource."""
        return self.data_source.read_component_states()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Refresh component health metrics from the observer state datasource.

        On success, component health and error flags are exported with `valid="1"`. On failure,
        the last known component list is exported with `valid="0"` while preserving last values
        when present.
        """
        states = metric_input
        components = [str(k) for k in states.keys()]
        if components:
            self._known_components = components
        for component, state in states.items():
            if state is None:
                continue
            base = {"component": str(component)}
            labels = {"component": str(component), "valid": "1"}
            self.replace_gauge_series(
                registry,
                self.HEALTH,
                1.0 if getattr(state, "is_healthy", False) else 0.0,
                match_labels=base,
                labels=labels,
                label_names=("component", "valid"),
            )
            self.replace_gauge_series(
                registry,
                self.ERRORS,
                1.0 if getattr(state, "has_errors", False) else 0.0,
                match_labels=base,
                labels=labels,
                label_names=("component", "valid"),
            )

    def collect_error_hook(self, registry, error: Exception) -> None:
        """Delegate failure handling to the stale hook by re-raising the datasource error."""
        raise error

    def collect_stale_hook(self, registry, error: Exception) -> None:
        """Export stale observer component health values under `valid=0` on refresh failure."""
        for component in self._known_components:
            base = {"component": str(component)}
            last_health = registry.gauge_get(
                self.HEALTH,
                labels={"component": str(component), "valid": "1"},
            )
            last_errors = registry.gauge_get(
                self.ERRORS,
                labels={"component": str(component), "valid": "1"},
            )
            if last_health is None:
                last_health = 0.0
            if last_errors is None:
                last_errors = 0.0
            labels = {"component": str(component), "valid": "0"}
            self.replace_gauge_series(
                registry,
                self.HEALTH,
                float(last_health),
                match_labels=base,
                labels=labels,
                label_names=("component", "valid"),
            )
            self.replace_gauge_series(
                registry,
                self.ERRORS,
                float(last_errors),
                match_labels=base,
                labels=labels,
                label_names=("component", "valid"),
            )
