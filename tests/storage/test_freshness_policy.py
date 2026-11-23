# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.queuefs.semantic_ops.freshness_policy import (
    FreshnessAction,
    decide_parent_refresh,
)


def _decide(**overrides):
    values = {
        "l0_body_changed": True,
        "has_freshness_baseline": True,
        "total_entries": 161,
        "pending_before": 3,
        "current_change_count": 1,
        "overview_sample_limit": 32,
        "refresh_ratio": 0.10,
    }
    values.update(overrides)
    return decide_parent_refresh(**values)


def test_unchanged_l0_stops_bubbling_without_incrementing_pending():
    decision = _decide(l0_body_changed=False)

    assert decision.action is FreshnessAction.NOOP
    assert decision.pending_after == 3


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"has_freshness_baseline": False}, FreshnessAction.REFRESH_NOW),
        ({"total_entries": 32}, FreshnessAction.REFRESH_NOW),
        ({"force_refresh": True}, FreshnessAction.REFRESH_NOW),
        ({}, FreshnessAction.MARK_PENDING),
        ({"pending_before": 16}, FreshnessAction.REFRESH_NOW),
    ],
)
def test_refresh_policy_boundaries(overrides, expected):
    assert _decide(**overrides).action is expected
