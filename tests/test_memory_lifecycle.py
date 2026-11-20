# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Memory hotness contracts used by the memory health statistics API."""

from datetime import datetime, timedelta, timezone

import pytest

from openviking.retrieve.memory_lifecycle import hotness_score

NOW = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)


def test_hotness_rises_with_activity_at_equal_recency():
    scores = [hotness_score(count, NOW, now=NOW) for count in (0, 10, 1000)]

    assert scores[0] == pytest.approx(0.5)
    assert scores[0] < scores[1] < scores[2] < 1.0
    assert scores[2] > 0.95


def test_hotness_halves_over_each_configured_half_life():
    fresh = hotness_score(5, NOW, now=NOW)
    assert hotness_score(5, NOW - timedelta(days=7), now=NOW) == pytest.approx(fresh / 2)
    assert hotness_score(5, NOW - timedelta(days=14), now=NOW) == pytest.approx(fresh / 4)
    assert hotness_score(5, NOW - timedelta(days=30), now=NOW, half_life_days=30) == pytest.approx(
        fresh / 2
    )
    assert hotness_score(100, None, now=NOW) == 0.0


def test_hotness_treats_legacy_naive_timestamps_as_utc():
    updated_at = NOW - timedelta(hours=1)
    expected = hotness_score(5, updated_at, now=NOW)

    assert hotness_score(5, updated_at.replace(tzinfo=None), now=NOW) == expected
    assert hotness_score(5, updated_at, now=NOW.replace(tzinfo=None)) == expected
