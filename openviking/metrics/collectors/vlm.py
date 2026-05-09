# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: VLMCollector.

Tracks VLM call count, duration, and token usage:
- Calls counter by provider/model
- Duration histogram by provider/model
- Token counters by provider/model

This collector is fed by VLMEventDataSource.record_call(...) events emitted from VLM code paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class VLMCollector(EventMetricCollector):
    """
    Translate VLM call events into per-provider/model counters and latency/token metrics.

    The collector consumes one normalized payload per completed VLM call and emits both
    per-call latency data and billing-style token totals.
    """

    DOMAIN: ClassVar[str] = "vlm"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_calls_total
    # e.g.: openviking_vlm_calls_total
    CALLS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "calls", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_call_duration_seconds
    # e.g.: openviking_vlm_call_duration_seconds
    CALL_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "call_duration", unit="seconds"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_tokens_input_total
    # e.g.: openviking_vlm_tokens_input_total
    TOKENS_INPUT_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "tokens_input", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_tokens_output_total
    # e.g.: openviking_vlm_tokens_output_total
    TOKENS_OUTPUT_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "tokens_output", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_tokens_total
    # e.g.: openviking_vlm_tokens_total
    TOKENS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "tokens", unit="total")

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"vlm.call"})

    def collect(self, registry=None) -> None:
        """
        Implement the unified collector interface as a no-op for this event-driven collector.

        VLM metrics are updated exclusively through `receive(...)`, so scrape-time pull
        collection does not need to do any work here.
        """
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Translate the supported VLM call event into counters and histogram samples.

        The hook forwards explicit account context from the payload so background VLM work can
        still land in the correct tenant partition.
        """
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
        """
        Record one VLM call as counters for calls and tokens plus a latency histogram sample.

        Input, output, and total token counters are all emitted because different dashboards care
        about cost attribution at different levels of granularity.
        """
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
        registry.inc_counter(
            self.TOKENS_INPUT_TOTAL,
            labels=labels,
            label_names=("provider", "model_name"),
            amount=int(prompt_tokens),
            account_id=account_id,
        )
        registry.inc_counter(
            self.TOKENS_OUTPUT_TOTAL,
            labels=labels,
            label_names=("provider", "model_name"),
            amount=int(completion_tokens),
            account_id=account_id,
        )
        registry.inc_counter(
            self.TOKENS_TOTAL,
            labels=labels,
            label_names=("provider", "model_name"),
            amount=int(prompt_tokens) + int(completion_tokens),
            account_id=account_id,
        )
