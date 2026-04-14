# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.probes import AsyncSystemProbeDataSource

from .base import CollectorConfig, ProbeMetricCollector


@dataclass
class AsyncSystemProbeCollector(ProbeMetricCollector):
    """
    Export readiness for individual async-system probe checks.

    The datasource returns a bounded mapping of probe names to booleans. This collector converts
    that mapping into one readiness gauge series per probe so dashboards can distinguish which
    async subsystem is currently unhealthy.
    """

    DOMAIN: ClassVar[str] = "async_system"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_readiness
    # e.g.: openviking_async_system_readiness
    READINESS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "readiness")

    data_source: AsyncSystemProbeDataSource
    config: CollectorConfig = CollectorConfig(timeout_seconds=0.5)
    # Keep the last-known probe set so failures can still emit `valid="0"` series.
    _known_probes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Initialize the last-known probe set for deterministic failure output."""
        if self._known_probes is None:
            self._known_probes = ["queue"]

    def read_metric_input(self):
        """Read the latest async-system probe mapping from the datasource."""
        return self.data_source.read_probe_state()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Publish one readiness sample per async-system probe name.

        The helper normalizes probe names to strings and writes a fixed 0/1 readiness gauge using
        `probe` as the only series label.
        """
        normalized = {str(k): bool(v) for k, v in metric_input.items()}
        self._known_probes = list(normalized.keys())
        for probe, is_ready in normalized.items():
            self.replace_gauge(
                registry,
                self.READINESS,
                1.0 if bool(is_ready) else 0.0,
                match_labels={"probe": str(probe)},
                labels={"probe": str(probe), "valid": "1"},
                label_names=("probe", "valid"),
            )

    def collect_stale_hook(self, registry, error: Exception) -> None:
        """Emit invalid readiness series for the last known probe set on failure."""
        for probe in self._known_probes:
            self.replace_gauge(
                registry,
                self.READINESS,
                0.0,
                match_labels={"probe": str(probe)},
                labels={"probe": str(probe), "valid": "0"},
                label_names=("probe", "valid"),
            )
