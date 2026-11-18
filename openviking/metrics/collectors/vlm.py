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
from openviking.metrics.datasources.model_usage import reported_token_count, reported_token_subset

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
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_ingestion_tokens_total
    # e.g.: openviking_vlm_ingestion_tokens_total
    INGESTION_TOKENS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "ingestion_tokens", unit="total"
    )

    # These are subsets of existing input/output counters, never extra tokens.
    USAGE_SUBSET_TOKENS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "usage_subset_tokens", unit="total"
    )
    USAGE_SUBSET_PARENT_TOKENS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "usage_subset_parent_tokens", unit="total"
    )
    USAGE_SUBSET_REPORTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "usage_subset_reports", unit="total"
    )

    USAGE_FIELD_REPORTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "usage_field_reports", unit="total"
    )

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
            prompt_tokens=reported_token_count(payload.get("prompt_tokens")),
            completion_tokens=reported_token_count(payload.get("completion_tokens")),
            error_code=str(payload.get("error_code") or "OK"),
            account_id=(
                None if payload.get("account_id") is None else str(payload.get("account_id"))
            ),
            source_type=payload.get("source_type"),
            prompt_cached_tokens=payload.get("prompt_cached_tokens"),
            completion_reasoning_tokens=payload.get("completion_reasoning_tokens"),
            usage_known_fields=payload.get("usage_known_fields"),
            observation_scope=payload.get("observation_scope", "legacy_usage"),
        )

    def record_call(
        self,
        registry,
        *,
        provider: str,
        model_name: str,
        duration_seconds: float,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        error_code: str = "OK",
        account_id: str | None = None,
        source_type: str | None = None,
        prompt_cached_tokens: int | None = None,
        completion_reasoning_tokens: int | None = None,
        usage_known_fields: list[str] | None = None,
        observation_scope: str = "legacy_usage",
    ) -> None:
        """
        Record one VLM call as counters for calls and tokens plus a latency histogram sample.

        Input, output, and total token counters are all emitted because different dashboards care
        about cost attribution at different levels of granularity.
        """
        prompt_tokens, completion_tokens = (
            reported_token_count(prompt_tokens),
            reported_token_count(completion_tokens),
        )
        prompt_cached_tokens = reported_token_subset(prompt_cached_tokens, prompt_tokens)
        completion_reasoning_tokens = reported_token_subset(
            completion_reasoning_tokens, completion_tokens
        )
        labels = {
            "provider": str(provider),
            "model_name": str(model_name),
            "error_code": str(error_code or "unknown"),
        }
        registry.inc_counter(
            self.CALLS_TOTAL,
            labels=labels,
            label_names=("provider", "model_name", "error_code"),
            account_id=account_id,
        )
        registry.observe_histogram(
            self.CALL_DURATION_SECONDS,
            float(duration_seconds),
            labels=labels,
            label_names=("provider", "model_name", "error_code"),
            account_id=account_id,
        )
        for field, value in (
            ("prompt_tokens", prompt_tokens),
            ("completion_tokens", completion_tokens),
            ("prompt_cached_tokens", prompt_cached_tokens),
            ("completion_reasoning_tokens", completion_reasoning_tokens),
        ):
            registry.inc_counter(
                self.USAGE_FIELD_REPORTS_TOTAL,
                labels={
                    "provider": str(provider),
                    "model_name": str(model_name),
                    "field": field,
                    "state": "known" if value is not None else "unknown",
                },
                label_names=("provider", "model_name", "field", "state"),
                account_id=account_id,
            )
        # Legacy failures never promised measured usage. New attempt observations
        # explicitly enumerate reported fields, including usage before a stream failure.
        if labels["error_code"] != "OK" and (
            usage_known_fields is None
            or observation_scope not in {"provider_attempt", "adapter_call"}
        ):
            return
        token_labels = {"provider": str(provider), "model_name": str(model_name)}
        for metric, amount in (
            (self.TOKENS_INPUT_TOTAL, prompt_tokens),
            (self.TOKENS_OUTPUT_TOTAL, completion_tokens),
        ):
            if amount is not None and amount > 0:
                registry.inc_counter(
                    metric,
                    labels=token_labels,
                    label_names=("provider", "model_name"),
                    amount=amount,
                    account_id=account_id,
                )
        for subset, value, parent in (
            ("cached_prompt", prompt_cached_tokens, prompt_tokens),
            ("reasoning_completion", completion_reasoning_tokens, completion_tokens),
        ):
            known = reported_token_subset(value, parent)
            if known is None or parent is None:
                continue
            subset_labels = {**token_labels, "subset": subset}
            for metric, amount in (
                (self.USAGE_SUBSET_TOKENS_TOTAL, known),
                (self.USAGE_SUBSET_PARENT_TOKENS_TOTAL, parent),
                (self.USAGE_SUBSET_REPORTS_TOTAL, 1),
            ):
                if amount <= 0:
                    continue
                registry.inc_counter(
                    metric,
                    labels=subset_labels,
                    label_names=("provider", "model_name", "subset"),
                    amount=amount,
                    account_id=account_id,
                )
        total_tokens = (
            prompt_tokens + completion_tokens
            if prompt_tokens is not None and completion_tokens is not None
            else 0
        )
        if total_tokens > 0:
            registry.inc_counter(
                self.TOKENS_TOTAL,
                labels=token_labels,
                label_names=("provider", "model_name"),
                amount=total_tokens,
                account_id=account_id,
            )
        if total_tokens > 0 and source_type:
            registry.inc_counter(
                self.INGESTION_TOKENS_TOTAL,
                labels={"source_type": str(source_type)},
                label_names=("source_type",),
                amount=total_tokens,
                account_id=account_id,
            )
