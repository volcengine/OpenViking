# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: SessionCollector.

Tracks session lifecycle and usage signals emitted from session-related code paths:
- create/get/delete/commit/extract lifecycle outcomes
- contexts and skills usage counts
- archive outcome (ok/skip)

Labels are bounded:
- action/status are small enums controlled by the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class SessionCollector(EventMetricCollector):
    """
    Translate session lifecycle and usage events into bounded session counters.

    The collector receives coarse-grained events from session management code paths and records
    only stable labels such as action and status so the exported series remain suitable for
    long-term dashboarding.
    """

    DOMAIN: ClassVar[str] = "session"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_lifecycle_total
    # e.g.: openviking_session_lifecycle_total
    LIFECYCLE_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "lifecycle", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_contexts_used_total
    # e.g.: openviking_session_contexts_used_total
    CONTEXTS_USED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN, "contexts_used", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_archive_total
    # e.g.: openviking_session_archive_total
    ARCHIVE_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "archive", unit="total")

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset(
        {
            "session.lifecycle",
            "session.contexts_used",
            "session.archive",
        }
    )

    def collect(self, registry=None) -> None:
        """Implement the collector interface as a no-op because session metrics are push-driven."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Dispatch one normalized session event to the matching counter update helper.

        Each branch handles a bounded event family emitted from session services and adapters.
        """
        if event_name == "session.lifecycle":
            action = payload.get("action")
            status = payload.get("status")
            if action is None or status is None:
                return
            self.record_lifecycle(
                registry,
                action=str(action),
                status=str(status),
            )
            return
        if event_name == "session.contexts_used":
            action = payload.get("action")
            delta = payload.get("delta")
            if action is None or delta is None:
                return
            self.record_contexts_used(
                registry,
                action=str(action),
                delta=int(delta),
            )
            return
        if event_name == "session.archive":
            status = payload.get("status")
            if status is None:
                return
            self.record_archive(
                registry,
                status=str(status),
            )

    def record_lifecycle(self, registry, *, action: str, status: str) -> None:
        """Increment the lifecycle counter for one session action/status pair."""
        registry.inc_counter(
            self.LIFECYCLE_TOTAL,
            labels={"action": str(action), "status": str(status)},
            label_names=("action", "status"),
        )

    def record_contexts_used(self, registry, *, action: str, delta: int) -> None:
        """Increase the contexts-used counter by the positive number of contexts consumed."""
        if delta <= 0:
            return
        registry.inc_counter(
            self.CONTEXTS_USED_TOTAL,
            labels={"action": str(action)},
            label_names=("action",),
            amount=int(delta),
        )

    def record_archive(self, registry, *, status: str) -> None:
        """Increment the archive outcome counter for one session-archive attempt."""
        registry.inc_counter(
            self.ARCHIVE_TOTAL,
            labels={"status": str(status)},
            label_names=("status",),
        )
