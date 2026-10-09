# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


class ModelRetryCollector(EventMetricCollector):
    """Export bounded model-call retry events as Prometheus counters."""

    EVENTS = {
        "model_retry.logical_call": (
            MetricCollector.metric_name("model", "logical_calls", unit="total"),
            ("result",),
        ),
        "model_retry.attempt": (
            MetricCollector.metric_name("model", "attempts", unit="total"),
            ("result", "error_class"),
        ),
        "model_retry.decision": (
            MetricCollector.metric_name("model", "retry_decisions", unit="total"),
            ("decision", "reason", "owner"),
        ),
        "model_retry.exhausted": (
            MetricCollector.metric_name("model", "retry_exhausted", unit="total"),
            ("reason",),
        ),
    }
    SUPPORTED_EVENTS = frozenset(EVENTS)

    def collect(self, registry=None) -> None:
        pass

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        name, extra_labels = self.EVENTS[event_name]
        label_names = ("model_type", "operation", "stage", *extra_labels)
        registry.inc_counter(
            name,
            labels={key: payload[key] for key in label_names},
            label_names=label_names,
        )
