# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import CollectorConfig, DomainStatsMetricCollector


def load_config():
    module = importlib.import_module("vikingbot.config.loader")
    return module.load_config()


def compute_feedback_stats(*args, **kwargs):
    module = importlib.import_module("vikingbot.observability.feedback_stats")
    return module.compute_feedback_stats(*args, **kwargs)


@dataclass
class FeedbackCollector(DomainStatsMetricCollector):
    """Export offline VikingBot feedback aggregates for Prometheus and Grafana."""

    DOMAIN: ClassVar[str] = "feedback"

    SESSIONS_SCANNED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "sessions_scanned", unit="total"
    )
    RESPONSES_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "responses", unit="total")
    TRACKED_RESPONSES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "tracked_responses", unit="total"
    )
    RESPONSES_WITH_FEEDBACK_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "responses_with_feedback", unit="total"
    )
    EVENTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "events", unit="total")
    THUMB_UP_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "thumb_up", unit="total")
    THUMB_DOWN_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "thumb_down", unit="total"
    )
    POSITIVE_OUTCOMES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "positive_outcomes", unit="total"
    )
    NEGATIVE_OUTCOMES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "negative_outcomes", unit="total"
    )
    REASKED_OUTCOMES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "reasked_outcomes", unit="total"
    )
    RESOLVED_OUTCOMES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "resolved_outcomes", unit="total"
    )
    FOLLOW_UP_WITHOUT_FEEDBACK_OUTCOMES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "follow_up_without_feedback_outcomes", unit="total"
    )
    COVERAGE: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "coverage")
    THUMBS_UP_RATE: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "thumbs_up_rate")
    THUMBS_DOWN_RATE: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "thumbs_down_rate")
    POSITIVE_FEEDBACK_RATE: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "positive_feedback_rate"
    )
    NEGATIVE_FEEDBACK_RATE: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "negative_feedback_rate"
    )
    REASK_RATE: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "reask_rate")
    ONE_TURN_RESOLUTION_RATE: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "one_turn_resolution_rate"
    )

    CHANNEL_RESPONSES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "channel_responses", unit="total"
    )
    CHANNEL_TRACKED_RESPONSES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "channel_tracked_responses", unit="total"
    )
    CHANNEL_EVENTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "channel_events", unit="total"
    )
    CHANNEL_NEGATIVE_OUTCOMES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "channel_negative_outcomes", unit="total"
    )
    CHANNEL_REASKED_OUTCOMES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "channel_reasked_outcomes", unit="total"
    )
    CHANNEL_COVERAGE: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "channel_coverage")
    CHANNEL_THUMBS_UP_RATE: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "channel_thumbs_up_rate"
    )
    CHANNEL_THUMBS_DOWN_RATE: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "channel_thumbs_down_rate"
    )
    CHANNEL_ONE_TURN_RESOLUTION_RATE: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "channel_one_turn_resolution_rate"
    )

    _SUMMARY_METRICS: ClassVar[tuple[tuple[str, str], ...]] = (
        (SESSIONS_SCANNED_TOTAL, "sessions_scanned"),
        (RESPONSES_TOTAL, "responses_total"),
        (TRACKED_RESPONSES_TOTAL, "tracked_responses_total"),
        (RESPONSES_WITH_FEEDBACK_TOTAL, "responses_with_feedback"),
        (EVENTS_TOTAL, "feedback_total"),
        (THUMB_UP_TOTAL, "thumb_up_total"),
        (THUMB_DOWN_TOTAL, "thumb_down_total"),
        (POSITIVE_OUTCOMES_TOTAL, "positive_feedback_total"),
        (NEGATIVE_OUTCOMES_TOTAL, "negative_feedback_total"),
        (REASKED_OUTCOMES_TOTAL, "reasked_total"),
        (RESOLVED_OUTCOMES_TOTAL, "resolved_total"),
        (FOLLOW_UP_WITHOUT_FEEDBACK_OUTCOMES_TOTAL, "follow_up_without_feedback_total"),
        (COVERAGE, "feedback_coverage"),
        (THUMBS_UP_RATE, "thumbs_up_rate"),
        (THUMBS_DOWN_RATE, "thumbs_down_rate"),
        (POSITIVE_FEEDBACK_RATE, "positive_feedback_rate"),
        (NEGATIVE_FEEDBACK_RATE, "negative_feedback_rate"),
        (REASK_RATE, "reask_rate"),
        (ONE_TURN_RESOLUTION_RATE, "one_turn_resolution_rate"),
    )

    _CHANNEL_METRICS: ClassVar[tuple[tuple[str, str], ...]] = (
        (CHANNEL_RESPONSES_TOTAL, "responses_total"),
        (CHANNEL_TRACKED_RESPONSES_TOTAL, "tracked_responses_total"),
        (CHANNEL_EVENTS_TOTAL, "feedback_total"),
        (CHANNEL_NEGATIVE_OUTCOMES_TOTAL, "negative_feedback_total"),
        (CHANNEL_REASKED_OUTCOMES_TOTAL, "reasked_total"),
        (CHANNEL_COVERAGE, "feedback_coverage"),
        (CHANNEL_THUMBS_UP_RATE, "thumbs_up_rate"),
        (CHANNEL_THUMBS_DOWN_RATE, "thumbs_down_rate"),
        (CHANNEL_ONE_TURN_RESOLUTION_RATE, "one_turn_resolution_rate"),
    )

    bot_data_path: Path | None = None
    config: CollectorConfig = CollectorConfig(ttl_seconds=30.0, timeout_seconds=1.0)
    _last_summary: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _last_channels: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    def read_metric_input(self) -> dict[str, Any]:
        """Read the latest persisted feedback aggregates from the bot session store."""
        return compute_feedback_stats(self._resolve_bot_data_path(), include_sessions=False)

    def collect_hook(self, registry, metric_input) -> None:
        """Export the latest summary and per-channel feedback gauges with `valid=1`."""
        self._last_summary = dict(metric_input.get("summary") or {})
        self._last_channels = {
            str(channel): dict(stats or {})
            for channel, stats in (metric_input.get("channels") or {}).items()
        }
        self._write(registry, valid="1", summary=self._last_summary, channels=self._last_channels)

    def collect_error_hook(self, registry, error: Exception) -> None:
        """Re-publish the last successful feedback snapshot with `valid=0` on failure."""
        self._write(registry, valid="0", summary=self._last_summary, channels=self._last_channels)

    def _resolve_bot_data_path(self) -> Path:
        if self.bot_data_path is not None:
            return Path(self.bot_data_path)
        return Path(load_config().bot_data_path)

    def _write(
        self,
        registry,
        *,
        valid: str,
        summary: dict[str, Any],
        channels: dict[str, dict[str, Any]],
    ) -> None:
        for metric_name, _ in self._SUMMARY_METRICS:
            registry.gauge_delete_matching(metric_name, match_labels={})
        for metric_name, key in self._SUMMARY_METRICS:
            registry.set_gauge(
                metric_name,
                float(summary.get(key, 0.0) or 0.0),
                labels={"valid": str(valid)},
                label_names=("valid",),
            )

        for metric_name, _ in self._CHANNEL_METRICS:
            registry.gauge_delete_matching(metric_name, match_labels={})
        for channel, channel_stats in channels.items():
            labels = {"channel": str(channel), "valid": str(valid)}
            for metric_name, key in self._CHANNEL_METRICS:
                registry.set_gauge(
                    metric_name,
                    float(channel_stats.get(key, 0.0) or 0.0),
                    labels=labels,
                    label_names=("channel", "valid"),
                )
