# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.observer_state import VikingDBStateDataSource

from .base import CollectorConfig, StateMetricCollector


@dataclass
class VikingDBCollector(StateMetricCollector):
    """
    Export VikingDB collection health and vector-count gauges with stale fallback semantics.

    The datasource reports one active collection snapshot at a time. This collector keeps the last
    successful collection label so a failed refresh can still publish `valid="0"` series that
    preserve dashboard continuity.
    """

    STALE_ON_ERROR: ClassVar[bool] = True

    DOMAIN: ClassVar[str] = "vikingdb"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_collection_health
    # e.g.: openviking_vikingdb_collection_health
    COLLECTION_HEALTH: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "collection_health")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_collection_vectors
    # e.g.: openviking_vikingdb_collection_vectors
    COLLECTION_VECTORS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "collection_vectors")

    data_source: VikingDBStateDataSource
    config: CollectorConfig = CollectorConfig(ttl_seconds=10.0, timeout_seconds=0.8)
    _last_collection: str = field(default="unknown", init=False, repr=False)

    def read_metric_input(self):
        """Read the latest VikingDB collection state from the datasource."""
        return self.data_source.read_vikingdb_state()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Refresh VikingDB gauges from the datasource.

        On success, gauges are exported with `valid="1"`. On failure, the collector falls back
        to the last observed collection name and preserves last vectors count when present,
        emitting `valid="0"` to mark the data as stale.
        """
        collection, ok, vectors = metric_input
        self._last_collection = str(collection)
        base = {"collection": str(collection)}
        labels = {"collection": str(collection), "valid": "1"}
        self.replace_gauge_series(
            registry,
            self.COLLECTION_HEALTH,
            1.0 if ok else 0.0,
            match_labels=base,
            labels=labels,
            label_names=("collection", "valid"),
        )
        self.replace_gauge_series(
            registry,
            self.COLLECTION_VECTORS,
            float(vectors),
            match_labels=base,
            labels=labels,
            label_names=("collection", "valid"),
        )

    def collect_error_hook(self, registry, error: Exception) -> None:
        """Delegate failure handling to the stale hook by re-raising the datasource error."""
        raise error

    def collect_stale_hook(self, registry, error: Exception) -> None:
        """Export stale VikingDB gauges under `valid=0` when datasource refresh fails."""
        collection = self._last_collection
        base = {"collection": collection}
        last_vectors = registry.gauge_get(
            self.COLLECTION_VECTORS,
            labels={"collection": collection, "valid": "1"},
        )
        if last_vectors is None:
            last_vectors = 0.0
        labels = {"collection": collection, "valid": "0"}
        self.replace_gauge_series(
            registry,
            self.COLLECTION_HEALTH,
            0.0,
            match_labels=base,
            labels=labels,
            label_names=("collection", "valid"),
        )
        self.replace_gauge_series(
            registry,
            self.COLLECTION_VECTORS,
            float(last_vectors),
            match_labels=base,
            labels=labels,
            label_names=("collection", "valid"),
        )
