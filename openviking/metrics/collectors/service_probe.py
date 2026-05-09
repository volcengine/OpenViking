# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.probes import ServiceProbeDataSource

from .base import CollectorConfig, ProbeMetricCollector


@dataclass
class ServiceProbeCollector(ProbeMetricCollector):
    """
    Export top-level service readiness gauges for core service dependencies.

    This probe collector intentionally keeps only two global gauges: overall service readiness and
    API key manager readiness. The values reflect current probe state rather than cumulative
    activity, so failed refreshes downgrade the gauges through the probe base helper.
    """

    DOMAIN_SERVICE: ClassVar[str] = "service"
    DOMAIN_API_KEY_MANAGER: ClassVar[str] = "api_key_manager"
    # rule: <METRICS_NAMESPACE>_<DOMAIN_SERVICE>_readiness
    # e.g.: openviking_service_readiness
    SERVICE_READINESS: ClassVar[str] = MetricCollector.metric_name(DOMAIN_SERVICE, "readiness")
    # rule: <METRICS_NAMESPACE>_<DOMAIN_API_KEY_MANAGER>_readiness
    # e.g.: openviking_api_key_manager_readiness
    API_KEY_MANAGER_READINESS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_API_KEY_MANAGER, "readiness"
    )

    data_source: ServiceProbeDataSource
    config: CollectorConfig = CollectorConfig(timeout_seconds=0.5)

    def read_metric_input(self):
        """Read the latest service-level readiness booleans from the datasource."""
        return self.data_source.read_probe_state()

    def collect_hook(self, registry, metric_input) -> None:
        """Translate the service readiness snapshot into validity-labelled 0/1 readiness gauges."""
        state = metric_input
        self.replace_gauge(
            registry,
            self.SERVICE_READINESS,
            1.0 if bool(state.get("service_readiness", False)) else 0.0,
            match_labels={},
            labels={"valid": "1"},
            label_names=("valid",),
        )
        self.replace_gauge(
            registry,
            self.API_KEY_MANAGER_READINESS,
            1.0 if bool(state.get("api_key_manager_readiness", False)) else 0.0,
            match_labels={},
            labels={"valid": "1"},
            label_names=("valid",),
        )

    def collect_stale_hook(self, registry, error: Exception) -> None:
        """Mark both service readiness gauges as stale when probe refresh cannot complete."""
        self.replace_gauge(
            registry,
            self.SERVICE_READINESS,
            0.0,
            match_labels={},
            labels={"valid": "0"},
            label_names=("valid",),
        )
        self.replace_gauge(
            registry,
            self.API_KEY_MANAGER_READINESS,
            0.0,
            match_labels={},
            labels={"valid": "0"},
            label_names=("valid",),
        )
