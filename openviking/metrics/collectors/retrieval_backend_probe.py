# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.probes import RetrievalBackendProbeDataSource

from .base import CollectorConfig, ProbeMetricCollector


@dataclass
class RetrievalBackendProbeCollector(ProbeMetricCollector):
    """
    Export readiness for each retrieval backend probe with explicit stale semantics.

    Successful refreshes publish one `valid="1"` gauge per probe name. If the datasource fails,
    the collector preserves the last observed probe set and republishes those series with
    `valid="0"` so monitoring can distinguish stale data from healthy zeros.
    """

    DOMAIN: ClassVar[str] = "retrieval_backend"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_readiness
    # e.g.: openviking_retrieval_backend_readiness
    READINESS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "readiness")

    data_source: RetrievalBackendProbeDataSource
    config: CollectorConfig = CollectorConfig(ttl_seconds=5.0, timeout_seconds=0.8)
    _known_probes: list[str] = field(default_factory=lambda: ["vikingdb"], init=False, repr=False)

    def read_metric_input(self):
        """Read the latest retrieval-backend probe mapping from the datasource."""
        return self.data_source.read_probe_state()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Refresh retrieval backend probe gauges.

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
        """Export invalid retrieval backend readiness gauges for the last known probes."""
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
