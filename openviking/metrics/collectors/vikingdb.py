# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.observer_state import VikingDBStateDataSource

from .base import CollectorConfig, StateMetricCollector


@dataclass(frozen=True)
class VikingDBSample:
    collection: str
    vectors: float
    synthetic: bool = False


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
    _last_samples: dict[str, VikingDBSample] = field(default_factory=dict, init=False, repr=False)

    def read_metric_input(self):
        """Read the latest VikingDB collection state from the datasource."""
        return self.data_source.read_vikingdb_state()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Refresh VikingDB gauges from the datasource.

        Healthy account samples are exported with `valid="1"`. If one account read fails inside
        an otherwise successful fan-out cycle, the collector reuses the last observed vector count
        for that account when available and emits `valid="0"` so dashboards can distinguish stale
        per-account data from a real zero-count collection.
        """
        current_samples: dict[str, VikingDBSample] = {}
        for account_id, collection, ok, vectors in metric_input:
            account = str(account_id)
            coll = str(collection)
            vec = float(vectors)
            previous = self._last_samples.get(account)

            if ok:
                current_samples[account] = VikingDBSample(collection=coll, vectors=vec)
                self._emit_account_gauges(registry, account, coll, 1.0, vec, "1")
                continue

            stale_vectors = vec
            if previous is not None and not previous.synthetic:
                stale_vectors = previous.vectors
            current_samples[account] = VikingDBSample(collection=coll, vectors=stale_vectors)
            self._emit_account_gauges(registry, account, coll, 0.0, stale_vectors, "0")

        # Remove series for accounts whose collection label changed since the last export.
        for account_id, sample in current_samples.items():
            previous = self._last_samples.get(account_id)
            if previous is None or previous.collection == sample.collection:
                continue
            old_collection = previous.collection
            base = {"collection": old_collection}
            registry.gauge_delete_matching(
                self.COLLECTION_HEALTH,
                match_labels=base,
                account_id=account_id,
            )
            registry.gauge_delete_matching(
                self.COLLECTION_VECTORS,
                match_labels=base,
                account_id=account_id,
            )

        # Remove series for accounts that disappeared from the latest datasource snapshot.
        for removed_account in set(self._last_samples) - set(current_samples):
            old_collection = self._last_samples[removed_account].collection
            base = {"collection": old_collection}
            registry.gauge_delete_matching(
                self.COLLECTION_HEALTH,
                match_labels=base,
                account_id=removed_account,
            )
            registry.gauge_delete_matching(
                self.COLLECTION_VECTORS,
                match_labels=base,
                account_id=removed_account,
            )
        self._last_samples = current_samples

    def collect_stale_hook(self, registry, error: Exception) -> None:
        """Export stale VikingDB gauges under `valid=0` when datasource refresh fails."""
        if not self._last_samples:
            self._last_samples = {
                "default": VikingDBSample(collection="default", vectors=0.0, synthetic=True)
            }
            self._emit_account_gauges(registry, "default", "default", 0.0, 0.0, "0")
            return
        for account_id, sample in self._last_samples.items():
            self._emit_account_gauges(registry, account_id, sample.collection, 0.0, sample.vectors, "0")

    def _emit_account_gauges(
        self,
        registry,
        account_id: str,
        collection: str,
        health: float,
        vectors: float,
        valid: str,
    ) -> None:
        base = {"collection": collection}
        labels = {"collection": collection, "valid": valid}
        self.replace_gauge_series(
            registry,
            self.COLLECTION_HEALTH,
            health,
            match_labels=base,
            labels=labels,
            label_names=("collection", "valid"),
            account_id=account_id,
        )
        self.replace_gauge_series(
            registry,
            self.COLLECTION_VECTORS,
            vectors,
            match_labels=base,
            labels=labels,
            label_names=("collection", "valid"),
            account_id=account_id,
        )
