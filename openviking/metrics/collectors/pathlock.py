# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Bounded counters for blocked durable pathlock recovery."""

from typing import ClassVar

from .base import EventMetricCollector


class PathLockRecoveryCollector(EventMetricCollector):
    """Export retry and exhaustion events without task/path label cardinality."""

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"pathlock.recovery_blocked"})

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        queue, outcome = payload.get("queue"), payload.get("outcome")
        if queue not in {"AddResource", "Reindex", "ContentWrite", "Semantic"}:
            return
        if outcome not in {"retry", "exhausted"}:
            return
        registry.inc_counter(
            "openviking_pathlock_recovery_blocked_total",
            labels={"queue": queue, "outcome": outcome},
            label_names=("queue", "outcome"),
        )
