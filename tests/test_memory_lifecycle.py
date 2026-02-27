# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for memory lifecycle hotness scoring (#296)."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from openviking.retrieve.memory_lifecycle import DEFAULT_HALF_LIFE_DAYS, hotness_score


NOW = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)


class TestHotnessScore:
    """Unit tests for hotness_score()."""

    def test_zero_active_count_just_now(self):
        """active_count=0, just updated -> sigmoid(log1p(0))=0.5, decay≈1.0."""
        score = hotness_score(0, NOW, now=NOW)
        assert 0.49 < score < 0.51  # sigmoid(0) = 0.5

    def test_high_active_count_just_now(self):
        """active_count=1000, just updated -> close to 1.0."""
        score = hotness_score(1000, NOW, now=NOW)
        assert score > 0.95

    def test_old_memory(self):
        """active_count=10, 30 days ago -> very low score."""
        old = NOW - timedelta(days=30)
        score = hotness_score(10, old, now=NOW)
        assert score < 0.1

    def test_recent_memory(self):
        """active_count=5, 1 hour ago -> moderate-high score."""
        recent = NOW - timedelta(hours=1)
        score = hotness_score(5, recent, now=NOW)
        assert 0.5 < score < 1.0

    def test_none_updated_at(self):
        """updated_at=None -> score must be 0.0."""
        score = hotness_score(100, None, now=NOW)
        assert score == 0.0

    def test_half_life_decay(self):
        """At exactly half_life_days, recency component should be ~0.5."""
        at_half = NOW - timedelta(days=DEFAULT_HALF_LIFE_DAYS)
        score = hotness_score(0, at_half, now=NOW)
        # freq = sigmoid(0) = 0.5, recency ≈ 0.5 => score ≈ 0.25
        assert 0.24 < score < 0.26

    def test_custom_half_life(self):
        """Custom half_life_days should change decay rate."""
        at_14_days = NOW - timedelta(days=14)
        score_7 = hotness_score(5, at_14_days, now=NOW, half_life_days=7.0)
        score_30 = hotness_score(5, at_14_days, now=NOW, half_life_days=30.0)
        # With half_life=30, decay is slower, so score should be higher
        assert score_30 > score_7

    def test_naive_datetime_treated_as_utc(self):
        """Timezone-naive datetimes should be handled without error."""
        naive_now = datetime(2026, 2, 26, 12, 0, 0)
        naive_updated = datetime(2026, 2, 26, 11, 0, 0)
        score = hotness_score(5, naive_updated, now=naive_now)
        assert 0.0 < score < 1.0

    def test_monotonic_with_active_count(self):
        """Higher active_count -> higher score (all else equal)."""
        s1 = hotness_score(1, NOW, now=NOW)
        s2 = hotness_score(10, NOW, now=NOW)
        s3 = hotness_score(100, NOW, now=NOW)
        assert s1 < s2 < s3

    def test_monotonic_with_recency(self):
        """More recent -> higher score (all else equal)."""
        s_old = hotness_score(5, NOW - timedelta(days=30), now=NOW)
        s_mid = hotness_score(5, NOW - timedelta(days=3), now=NOW)
        s_new = hotness_score(5, NOW - timedelta(hours=1), now=NOW)
        assert s_old < s_mid < s_new


class TestHotnessBlending:
    """Tests for the blending logic (alpha weighting)."""

    def test_alpha_zero_preserves_semantic_order(self):
        """With alpha=0, final score equals semantic score exactly."""
        semantic = 0.85
        alpha = 0.0
        h = hotness_score(100, NOW, now=NOW)
        blended = (1 - alpha) * semantic + alpha * h
        assert blended == pytest.approx(semantic)

    def test_hotness_boost_can_rerank(self):
        """A hot memory with lower semantic score can overtake a cold one."""
        alpha = 0.4  # aggressive weight for demonstration

        # Memory A: high semantic, cold (old, low access)
        sem_a = 0.8
        h_a = hotness_score(1, NOW - timedelta(days=60), now=NOW)
        blended_a = (1 - alpha) * sem_a + alpha * h_a

        # Memory B: lower semantic, hot (recent, high access)
        sem_b = 0.6
        h_b = hotness_score(500, NOW, now=NOW)
        blended_b = (1 - alpha) * sem_b + alpha * h_b

        # B should overtake A due to hotness
        assert blended_b > blended_a

    def test_default_alpha_preserves_semantic_dominance(self):
        """With default alpha=0.2, a large semantic gap is not overturned."""
        alpha = 0.2

        # Memory A: much higher semantic, cold
        sem_a = 0.9
        h_a = hotness_score(0, NOW - timedelta(days=30), now=NOW)
        blended_a = (1 - alpha) * sem_a + alpha * h_a

        # Memory B: much lower semantic, hot
        sem_b = 0.3
        h_b = hotness_score(1000, NOW, now=NOW)
        blended_b = (1 - alpha) * sem_b + alpha * h_b

        # A should still win — semantic dominance preserved
        assert blended_a > blended_b
