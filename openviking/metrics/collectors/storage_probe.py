# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.probes import StorageProbeDataSource

from .base import CollectorConfig, ProbeMetricCollector


@dataclass
class StorageProbeCollector(ProbeMetricCollector):
    """
    Export readiness for each storage probe with explicit stale-data semantics.

    Storage availability may degrade independently by backend, so the collector keeps one series
    per probe name and republishes the last observed probe set with `valid="0"` if refreshes fail.
    """

    DOMAIN: ClassVar[str] = "storage"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_readiness
    # e.g.: openviking_storage_readiness
    READINESS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "readiness")

    data_source: StorageProbeDataSource
    config: CollectorConfig = CollectorConfig(ttl_seconds=5.0, timeout_seconds=0.8)
    _known_probes: list[str] = field(default_factory=lambda: ["agfs"], init=False, repr=False)

    def read_metric_input(self):
        """Read the latest storage probe mapping from the datasource."""
        return self.data_source.read_probe_state()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Refresh storage probe gauges.

        On success, each probe is exported with `valid="1"`. On failure, the last known probe
        set is exported with `valid="0"` so monitoring can detect staleness.
        """
        state = metric_input
        probes = [str(k) for k in state.keys()]
        if probes:
            self._known_probes = probes
        for probe, ok in state.items():
            base = {"probe": str(probe)}
            self.replace_gauge(
                registry,
                self.READINESS,
                1.0 if ok else 0.0,
                match_labels=base,
                labels={"probe": str(probe), "valid": "1"},
                label_names=("probe", "valid"),
            )

    def collect_error_hook(self, registry, error: Exception) -> None:
        """Export invalid storage readiness gauges for the last known probes on failure."""
        for probe in self._known_probes:
            base = {"probe": str(probe)}
            self.replace_gauge(
                registry,
                self.READINESS,
                0.0,
                match_labels=base,
                labels={"probe": str(probe), "valid": "0"},
                label_names=("probe", "valid"),
            )
