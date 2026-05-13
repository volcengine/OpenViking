# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.observer_state import LockStateDataSource

from .base import CollectorConfig, StateMetricCollector


@dataclass
class LockCollector(StateMetricCollector):
    """
    Translate lock-manager snapshots into lock-related gauge families.

    The datasource returns a point-in-time view of active, waiting, and stale locks. This
    collector mirrors that snapshot directly as gauges because the values represent current state
    rather than cumulative activity.
    """

    DOMAIN: ClassVar[str] = "lock"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_active
    # e.g.: openviking_lock_active
    ACTIVE: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "active")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_waiting
    # e.g.: openviking_lock_waiting
    WAITING: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "waiting")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_stale
    # e.g.: openviking_lock_stale
    STALE: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "stale")

    data_source: LockStateDataSource
    config: CollectorConfig = CollectorConfig(timeout_seconds=0.5)

    def read_metric_input(self):
        """Read the latest lock-manager state counters from the datasource."""
        return self.data_source.read_lock_state()

    def collect_hook(self, registry, metric_input) -> None:
        """Publish the current lock snapshot as active, waiting, and stale gauges."""
        active, waiting, stale = metric_input
        registry.set_gauge(self.ACTIVE, float(active))
        registry.set_gauge(self.WAITING, float(waiting))
        registry.set_gauge(self.STALE, float(stale))
