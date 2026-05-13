# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.probes import ModelProviderProbeDataSource

from .base import CollectorConfig, ProbeMetricCollector


@dataclass
class ModelProviderProbeCollector(ProbeMetricCollector):
    """
    Export model-provider readiness for the currently selected backend provider.

    The datasource reports which provider is active and whether it is currently healthy. The
    collector keeps the last successful provider label so failures can still emit a stale
    `valid="0"` replacement series for the same provider dimension.
    """

    DOMAIN: ClassVar[str] = "model_provider"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_readiness
    # e.g.: openviking_model_provider_readiness
    READINESS: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "readiness")

    data_source: ModelProviderProbeDataSource
    config: CollectorConfig = CollectorConfig(ttl_seconds=10.0, timeout_seconds=0.5)
    _last_provider: str = field(default="unknown", init=False, repr=False)

    def read_metric_input(self):
        """Read the latest model-provider readiness snapshot from the datasource."""
        return self.data_source.read_probe_state()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Refresh model provider probe gauges.

        On success, the current provider readiness is exported with `valid="1"`. On failure,
        the last known provider is exported with `valid="0"` so monitoring can detect staleness.
        """
        state = metric_input
        provider, ok = state.get("provider", ("unknown", False))
        provider = str(provider)
        self._last_provider = provider
        base = {"provider": provider}
        self.replace_gauge(
            registry,
            self.READINESS,
            1.0 if ok else 0.0,
            match_labels=base,
            labels={"provider": provider, "valid": "1"},
            label_names=("provider", "valid"),
        )

    def collect_error_hook(self, registry, error: Exception) -> None:
        """Export invalid model-provider readiness using the last known provider label."""
        provider = self._last_provider
        base = {"provider": provider}
        self.replace_gauge(
            registry,
            self.READINESS,
            0.0,
            match_labels=base,
            labels={"provider": provider, "valid": "0"},
            label_names=("provider", "valid"),
        )
