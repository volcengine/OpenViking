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
    Export async-system readiness and Python default executor metrics.

    Readiness remains one gauge series per probe. Executor metrics use `pool`,
    `process_role`, and `worker` labels.
    """

    DOMAIN: ClassVar[str] = "async_system"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_readiness
    # e.g.: openviking_async_system_readiness
    READINESS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "readiness")
    EXECUTOR_GAUGES: ClassVar[tuple[tuple[str, str], ...]] = (
        (MetricCollector.metric_name("executor", "max_workers"), "max_workers"),
        (MetricCollector.metric_name("executor", "threads"), "threads"),
        (MetricCollector.metric_name("executor", "active_tasks"), "active_tasks"),
        (MetricCollector.metric_name("executor", "pending_tasks"), "pending_tasks"),
    )
    EXECUTOR_COUNTERS: ClassVar[tuple[tuple[str, str], ...]] = (
        (MetricCollector.metric_name("executor", "submitted", unit="total"), "submitted_total"),
        (MetricCollector.metric_name("executor", "completed", unit="total"), "completed_total"),
        (MetricCollector.metric_name("executor", "failed", unit="total"), "failed_total"),
    )

    data_source: AsyncSystemProbeDataSource
    config: CollectorConfig = CollectorConfig(timeout_seconds=0.5)
    # Keep the last-known probe set so failures can still emit `valid="0"` series.
    _known_probes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Initialize the last-known probe set for deterministic failure output."""
        if self._known_probes is None:
            self._known_probes = ["queue"]

    def read_metric_input(self):
        """Read the latest async-system probe and executor metrics from the datasource."""
        return self.data_source.read_async_system_state()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Publish readiness samples and default-executor runtime samples.

        The readiness helper normalizes probe names to strings and writes a fixed 0/1 readiness
        gauge. Executor samples keep a low-cardinality `pool/process_role/worker` label set.
        """
        probes = metric_input["probes"]
        probes_valid = bool(metric_input["probes_valid"])
        executor = metric_input["executor"]
        normalized = {str(k): bool(v) for k, v in probes.items()}
        self._known_probes = list(normalized.keys())
        self._write_readiness(registry, normalized, valid=probes_valid)
        self._write_executor_metrics(registry, executor)

    def _write_readiness(self, registry, probes: dict[str, bool], *, valid: bool) -> None:
        """Write readiness gauges for async-system probes."""
        for probe, is_ready in probes.items():
            self.replace_gauge(
                registry,
                self.READINESS,
                1.0 if valid and bool(is_ready) else 0.0,
                match_labels={"probe": str(probe)},
                labels={"probe": str(probe), "valid": "1" if valid else "0"},
                label_names=("probe", "valid"),
            )

    def _write_executor_metrics(self, registry, metrics: dict[str, int | str]) -> None:
        """Write Python default executor metrics using stable labels."""
        labels = {
            "pool": str(metrics["pool"]),
            "process_role": str(metrics["process_role"]),
            "worker": str(metrics["worker"]),
        }
        label_names = ("pool", "process_role", "worker")
        for metric_name, key in self.EXECUTOR_GAUGES:
            registry.set_gauge(
                metric_name,
                float(metrics[key]),
                labels=labels,
                label_names=label_names,
            )
        for metric_name, key in self.EXECUTOR_COUNTERS:
            registry.set_counter(
                metric_name,
                float(metrics[key]),
                labels=labels,
                label_names=label_names,
            )

    def collect_stale_hook(self, registry, error: Exception) -> None:
        """Emit invalid readiness series for the last known probe set on failure."""
        self._write_readiness(
            registry,
            {str(probe): False for probe in self._known_probes},
            valid=False,
        )
