# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: RerankCollector.

Tracks rerank call count, duration, and token usage:
- Calls counter by provider/model
- Duration histogram by provider/model
- Token counters by provider/model

This collector is fed by `RerankEventDataSource.record_call(...)` events emitted from rerank code
paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class RerankCollector(EventMetricCollector):
    """
    Translate rerank call events into per-provider/model counters and latency/token metrics.

    The collector mirrors the VLM per-call metric family so rerank can participate in the same
    dashboard patterns without introducing provider-specific series names.
    """

    DOMAIN: ClassVar[str] = "rerank"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_calls_total
    # e.g.: openviking_rerank_calls_total
    CALLS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "calls", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_call_duration_seconds
    # e.g.: openviking_rerank_call_duration_seconds
    CALL_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "call_duration", unit="seconds"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_tokens_input_total
    # e.g.: openviking_rerank_tokens_input_total
    TOKENS_INPUT_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "tokens_input", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_tokens_output_total
    # e.g.: openviking_rerank_tokens_output_total
    TOKENS_OUTPUT_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "tokens_output", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_tokens_total
    # e.g.: openviking_rerank_tokens_total
    TOKENS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "tokens", unit="total")

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"rerank.call"})

    def collect(self, registry=None) -> None:
        """Implement the unified collector interface as a no-op for this event-driven collector."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """Translate one rerank-call payload into counters and histogram samples."""
        self.record_call(
            registry,
            provider=str(payload["provider"]),
            model_name=str(payload["model_name"]),
            duration_seconds=float(payload["duration_seconds"]),
            prompt_tokens=int(payload["prompt_tokens"]),
            completion_tokens=int(payload["completion_tokens"]),
            account_id=(
                None if payload.get("account_id") is None else str(payload.get("account_id"))
            ),
        )

    def record_call(
        self,
        registry,
        *,
        provider: str,
        model_name: str,
        duration_seconds: float,
        prompt_tokens: int,
        completion_tokens: int,
        account_id: str | None = None,
    ) -> None:
        """Record one rerank call as calls/tokens counters plus a latency histogram sample."""
        labels = {"provider": str(provider), "model_name": str(model_name)}
        registry.inc_counter(
            self.CALLS_TOTAL,
            labels=labels,
            label_names=("provider", "model_name"),
            account_id=account_id,
        )
        registry.observe_histogram(
            self.CALL_DURATION_SECONDS,
            float(duration_seconds),
            labels=labels,
            label_names=("provider", "model_name"),
            account_id=account_id,
        )
        if int(prompt_tokens) > 0:
            registry.inc_counter(
                self.TOKENS_INPUT_TOTAL,
                labels=labels,
                label_names=("provider", "model_name"),
                amount=int(prompt_tokens),
                account_id=account_id,
            )
        if int(completion_tokens) > 0:
            registry.inc_counter(
                self.TOKENS_OUTPUT_TOTAL,
                labels=labels,
                label_names=("provider", "model_name"),
                amount=int(completion_tokens),
                account_id=account_id,
            )
        total_tokens = int(prompt_tokens) + int(completion_tokens)
        if total_tokens > 0:
            registry.inc_counter(
                self.TOKENS_TOTAL,
                labels=labels,
                label_names=("provider", "model_name"),
                amount=total_tokens,
                account_id=account_id,
            )
