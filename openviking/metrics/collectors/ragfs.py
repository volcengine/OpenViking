# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Publish authoritative native metrics through the shared Registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from openviking.metrics.core.base import ReadEnvelope
from openviking.metrics.datasources.ragfs import RagfsMetricDataSource

from .base import CollectorConfig, DomainStatsMetricCollector


@dataclass
class RagfsMetricCollector(DomainStatsMetricCollector):
    """Replace native metric values without computing increments or retaining previous values."""

    data_source: RagfsMetricDataSource
    config: CollectorConfig = CollectorConfig(ttl_seconds=None, timeout_seconds=1.0)
    _collect_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _tracked_series: set[tuple[str, str, tuple[tuple[str, str], ...]]] = field(
        default_factory=set, init=False, repr=False
    )

    def collect(self, registry) -> bool | None:
        """Collect into registry; return False on overlap, or None after completion."""
        if not self._collect_lock.acquire(blocking=False):
            return False
        try:
            super().collect(registry)
        finally:
            self._collect_lock.release()

    def read_metric_input(self) -> ReadEnvelope[list[dict[str, object]]]:
        """Return the datasource's validated current native records."""
        return self.data_source.read_metrics()

    def collect_hook(self, registry, metric_input: list[dict[str, object]]) -> None:
        """Replace validated records in registry, then delete absent series; return None."""
        current_series = set()
        for record in metric_input:
            name = f"{self.METRICS_NAMESPACE}_{record['name']}"
            kind = record["type"]
            labels = record["labels"]
            label_names = tuple(sorted(labels))
            if kind == "counter":
                registry.set_counter(
                    name,
                    float(record["value"]) * record["scale"],
                    labels=labels,
                    label_names=label_names,
                )
            elif kind == "gauge":
                registry.set_gauge(
                    name,
                    record["value"],
                    labels=labels,
                    label_names=label_names,
                )
            else:
                registry.set_histogram(
                    name,
                    bucket_bounds=[
                        float(bound) * record["scale"] for bound in record["bucket_bounds"]
                    ],
                    bucket_counts=record["bucket_counts"],
                    count=record["count"],
                    value_sum=float(record["sum"]) * record["scale"],
                    labels=labels,
                    label_names=label_names,
                )
            identity = (kind, name, tuple(sorted(labels.items())))
            current_series.add(identity)
            self._tracked_series.add(identity)
        for identity in sorted(self._tracked_series - current_series):
            kind, name, labels = identity
            getattr(registry, f"{kind}_delete_matching")(name, match_labels=dict(labels))
            self._tracked_series.remove(identity)
