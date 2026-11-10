# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pure policy for freshness-aware directory summary refreshes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FreshnessAction(str, Enum):
    """Scheduling result for one direct-child semantic change."""

    NOOP = "noop"
    MARK_PENDING = "mark_pending"
    REFRESH_NOW = "refresh_now"


@dataclass(frozen=True)
class FreshnessDecision:
    """Policy output plus the counter snapshot used to reach it."""

    action: FreshnessAction
    pending_after: int
    total_entries: int


def decide_parent_refresh(
    *,
    l0_body_changed: bool,
    has_freshness_baseline: bool,
    total_entries: int,
    pending_before: int,
    current_change_count: int,
    overview_sample_limit: int,
    refresh_ratio: float,
    force_refresh: bool = False,
) -> FreshnessDecision:
    """Decide whether a parent aggregation should run.

    ``pending_child_changes`` intentionally counts events rather than unique
    children. For wide directories the equivalent integer threshold is
    ``ceil(refresh_ratio * total_entries)``; comparing the ratio directly
    avoids carrying another derived configuration value.
    """

    if not l0_body_changed or current_change_count <= 0:
        return FreshnessDecision(FreshnessAction.NOOP, pending_before, total_entries)

    pending_after = pending_before + current_change_count
    if not has_freshness_baseline:
        return FreshnessDecision(FreshnessAction.REFRESH_NOW, pending_after, total_entries)
    if force_refresh or total_entries <= overview_sample_limit:
        return FreshnessDecision(FreshnessAction.REFRESH_NOW, pending_after, total_entries)

    change_ratio = pending_after / max(total_entries, 1)
    action = (
        FreshnessAction.REFRESH_NOW
        if change_ratio >= refresh_ratio
        else FreshnessAction.MARK_PENDING
    )
    return FreshnessDecision(action, pending_after, total_entries)
