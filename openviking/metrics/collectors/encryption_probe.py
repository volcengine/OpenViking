# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.encryption import EncryptionProbeDataSource

from .base import CollectorConfig, ProbeMetricCollector


@dataclass
class EncryptionProbeCollector(ProbeMetricCollector):
    """
    Export encryption bootstrap and provider readiness as probe-style gauges.

    Successful refreshes publish `valid="1"` series for overall encryption health, root-key
    availability, and the currently active KMS provider. Failed refreshes preserve the last known
    provider label and re-publish the families with `valid="0"` so stale states remain observable.
    """

    DOMAIN: ClassVar[str] = "encryption"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_component_health
    # e.g.: openviking_encryption_component_health
    COMPONENT_HEALTH: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "component_health")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_root_key_ready
    # e.g.: openviking_encryption_root_key_ready
    ROOT_KEY_READY: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "root_key_ready")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_kms_provider_ready
    # e.g.: openviking_encryption_kms_provider_ready
    KMS_PROVIDER_READY: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "kms_provider_ready")

    data_source: EncryptionProbeDataSource
    config: CollectorConfig = CollectorConfig(ttl_seconds=10.0, timeout_seconds=0.5)
    _last_provider: str = field(default="unknown", init=False, repr=False)

    def read_metric_input(self):
        """Read the current encryption bootstrap readiness tuple from the datasource."""
        return self.data_source.read_probe_state()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Refresh encryption probe gauges.

        On success, gauges are exported with `valid="1"`. On failure, the exporter emits
        `valid="0"` using the last known provider so dashboards can detect staleness.
        """
        ok_component, provider = metric_input
        provider = str(provider)
        self._last_provider = provider
        self.replace_gauge(
            registry,
            self.COMPONENT_HEALTH,
            1.0 if ok_component else 0.0,
            match_labels={},
            labels={"valid": "1"},
            label_names=("valid",),
        )
        self.replace_gauge(
            registry,
            self.ROOT_KEY_READY,
            1.0 if ok_component else 0.0,
            match_labels={},
            labels={"valid": "1"},
            label_names=("valid",),
        )
        self.replace_gauge(
            registry,
            self.KMS_PROVIDER_READY,
            1.0 if ok_component else 0.0,
            match_labels={"provider": provider},
            labels={"provider": provider, "valid": "1"},
            label_names=("provider", "valid"),
        )

    def collect_error_hook(self, registry, error: Exception) -> None:
        """
        Export invalid encryption readiness gauges after a failed probe refresh.

        The collector falls back to the last successful provider label so the stale replacement
        series targets the same Prometheus label set.
        """
        provider = self._last_provider
        self.replace_gauge(
            registry,
            self.COMPONENT_HEALTH,
            0.0,
            match_labels={},
            labels={"valid": "0"},
            label_names=("valid",),
        )
        self.replace_gauge(
            registry,
            self.ROOT_KEY_READY,
            0.0,
            match_labels={},
            labels={"valid": "0"},
            label_names=("valid",),
        )
        self.replace_gauge(
            registry,
            self.KMS_PROVIDER_READY,
            0.0,
            match_labels={"provider": provider},
            labels={"provider": provider, "valid": "0"},
            label_names=("provider", "valid"),
        )
