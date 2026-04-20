# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
DomainStats collector: ModelUsageCollector.

This collector exports aggregated model usage in a Prometheus-friendly way:
- Input source is cumulative usage (calls/tokens) from model instances or shared token trackers.
- Output is Prometheus Counters, which must be monotonic.

To bridge cumulative -> monotonic, we store the last seen cumulative values in memory and
increment Prometheus Counters by deltas on each refresh.

Failure semantics:
- For each model_type (vlm/embedding/rerank), we export an availability gauge with a `valid`
  label describing whether the sample is fresh (`valid="1"`) or stale fallback (`valid="0"`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector
from openviking.metrics.datasources.model_usage import ModelUsageDataSource

from .base import CollectorConfig, DomainStatsMetricCollector


@dataclass
class ModelUsageCollector(DomainStatsMetricCollector):
    """
    Export aggregated model-usage snapshots as availability gauges and monotonic counters.

    The datasource provides cumulative usage grouped by model type, model name, and provider. This
    collector translates those cumulative values into Prometheus counters by storing the last seen
    totals and incrementing only the positive delta for each label tuple. Availability is exported
    separately so dashboards can distinguish "usage unavailable" from "stale fallback sample".
    """

    DOMAIN_MODEL_USAGE: ClassVar[str] = "model_usage"
    DOMAIN_MODEL: ClassVar[str] = "model"
    # rule: <METRICS_NAMESPACE>_<DOMAIN_MODEL_USAGE>_available
    # e.g.: openviking_model_usage_available
    USAGE_AVAILABLE: ClassVar[str] = MetricCollector.metric_name(DOMAIN_MODEL_USAGE, "available")
    # rule: <METRICS_NAMESPACE>_<DOMAIN_MODEL>_calls_total
    # e.g.: openviking_model_calls_total
    CALLS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN_MODEL, "calls", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN_MODEL>_tokens_total
    # e.g.: openviking_model_tokens_total
    TOKENS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN_MODEL, "tokens", unit="total")

    data_source: ModelUsageDataSource
    config: CollectorConfig = CollectorConfig(ttl_seconds=10.0, timeout_seconds=0.8)
    _last_available_by_type: dict[str, float] = field(
        default_factory=lambda: {"vlm": 0.0, "embedding": 0.0, "rerank": 0.0},
        init=False,
        repr=False,
    )

    def read_metric_input(self):
        """Read the aggregated model-usage snapshot from the datasource."""
        return self.data_source.read_model_usage()

    def collect_hook(self, registry, metric_input) -> None:
        """
        Refresh model usage metrics from one fresh aggregated snapshot.

        For counters, we apply deltas based on the last seen cumulative usage.
        If the cumulative value decreases (e.g., process restart / reset), we treat the
        current cumulative value as the delta to keep exported counters monotonic.

        Availability is exported as `openviking_model_usage_available`, where the metric value
        describes whether usage data is currently available and `valid="1"` marks the sample as
        fresh.
        """
        usage = metric_input
        for model_type in ("vlm", "embedding", "rerank"):
            per_type = None if not isinstance(usage, dict) else usage.get(model_type)
            available = 0.0
            usage_by_model = {}
            if isinstance(per_type, dict):
                available = 1.0 if bool(per_type.get("available")) else 0.0
                raw_usage_by_model = per_type.get("usage_by_model") or {}
                if isinstance(raw_usage_by_model, dict):
                    usage_by_model = raw_usage_by_model

            self._last_available_by_type[model_type] = available
            self._write_availability(
                registry,
                model_type=model_type,
                available=available,
                valid="1",
            )

            for model_name, model_usage in usage_by_model.items():
                if not isinstance(model_usage, dict):
                    continue
                usage_by_provider = model_usage.get("usage_by_provider") or {}
                if not isinstance(usage_by_provider, dict):
                    continue
                for provider, provider_usage in usage_by_provider.items():
                    if not isinstance(provider_usage, dict):
                        continue
                    self._apply_provider_usage(
                        registry,
                        model_type=str(model_type),
                        provider=str(provider),
                        model_name=str(model_name),
                        provider_usage=provider_usage,
                    )

    def collect_error_hook(self, registry, error: Exception) -> None:
        """
        Re-emit the last known availability state as stale fallback samples.

        Failed refreshes downgrade the availability samples to `valid="0"` but do not mutate the
        monotonic usage counters, because no fresh cumulative snapshot was obtained.
        """
        for model_type in ("vlm", "embedding", "rerank"):
            self._write_availability(
                registry,
                model_type=model_type,
                available=float(self._last_available_by_type.get(model_type, 0.0)),
                valid="0",
            )

    def _write_availability(
        self,
        registry,
        *,
        model_type: str,
        available: float,
        valid: str,
    ) -> None:
        """
        Replace the availability gauge series for one model type with a fresh validity label.

        The replacement deletes any prior series for the same `model_type`, ensuring only one of
        `valid="1"` or `valid="0"` remains visible at a time.
        """
        registry.gauge_delete_matching(
            self.USAGE_AVAILABLE,
            match_labels={"model_type": str(model_type)},
        )
        registry.set_gauge(
            self.USAGE_AVAILABLE,
            float(available),
            labels={"model_type": str(model_type), "valid": str(valid)},
            label_names=("model_type", "valid"),
        )

    def _apply_provider_usage(
        self,
        registry,
        *,
        model_type: str,
        provider: str,
        model_name: str,
        provider_usage: dict,
    ) -> None:
        """
        Apply one provider-specific usage snapshot as counter deltas.

        The helper keeps counter monotonicity local to one `(model_type, provider, model_name)`
        tuple and then repeats the same delta conversion for each token subtype.
        """
        call_count = int(provider_usage.get("call_count", 0) or 0)
        self.inc_counter_from_cumulative(
            registry=registry,
            metric_name=self.CALLS_TOTAL,
            key=(self.CALLS_TOTAL, model_type, provider, model_name),
            current_value=call_count,
            labels={"model_type": model_type, "provider": provider, "model_name": model_name},
            label_names=("model_name", "model_type", "provider"),
        )

        tokens = {
            "prompt": int(provider_usage.get("prompt_tokens", 0) or 0),
            "completion": int(provider_usage.get("completion_tokens", 0) or 0),
            "total": int(provider_usage.get("total_tokens", 0) or 0),
        }
        for token_type, value in tokens.items():
            self.inc_counter_from_cumulative(
                registry=registry,
                metric_name=self.TOKENS_TOTAL,
                key=(self.TOKENS_TOTAL, model_type, provider, model_name, token_type),
                current_value=value,
                labels={
                    "model_type": model_type,
                    "provider": provider,
                    "model_name": model_name,
                    "token_type": token_type,
                },
                label_names=("model_name", "model_type", "provider", "token_type"),
            )
