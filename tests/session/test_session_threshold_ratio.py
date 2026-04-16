# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for ratio-based auto-commit threshold (issue #1172)."""

from unittest.mock import MagicMock

import pytest

from openviking.session.session import Session


def _make_session(**kwargs):
    """Create a Session with minimal mocked dependencies."""
    viking_fs = MagicMock()
    return Session(viking_fs=viking_fs, **kwargs)


class TestEffectiveCommitThreshold:
    """Test effective_commit_threshold property."""

    def test_default_threshold(self):
        """Default threshold is 8000 when no ratio or context_window."""
        session = _make_session()
        assert session.effective_commit_threshold == 8000

    def test_fixed_threshold(self):
        """Fixed threshold is used when no ratio is set."""
        session = _make_session(auto_commit_threshold=50000)
        assert session.effective_commit_threshold == 50000

    def test_ratio_with_context_window(self):
        """Ratio * context_window is used when both are provided."""
        session = _make_session(
            auto_commit_threshold_ratio=0.38,
            context_window=1_000_000,
        )
        assert session.effective_commit_threshold == 380000

    def test_ratio_overrides_fixed(self):
        """Ratio takes precedence over fixed threshold."""
        session = _make_session(
            auto_commit_threshold=20000,
            auto_commit_threshold_ratio=0.38,
            context_window=200_000,
        )
        assert session.effective_commit_threshold == 76000

    def test_ratio_without_context_window_falls_back(self):
        """Ratio alone (no context_window) falls back to fixed threshold."""
        session = _make_session(
            auto_commit_threshold=15000,
            auto_commit_threshold_ratio=0.38,
        )
        assert session.effective_commit_threshold == 15000

    def test_context_window_without_ratio_falls_back(self):
        """context_window alone (no ratio) falls back to fixed threshold."""
        session = _make_session(
            auto_commit_threshold=15000,
            context_window=1_000_000,
        )
        assert session.effective_commit_threshold == 15000

    def test_small_model(self):
        """Ratio works for small models (200K context)."""
        session = _make_session(
            auto_commit_threshold_ratio=0.38,
            context_window=200_000,
        )
        assert session.effective_commit_threshold == 76000


class TestThresholdValidation:
    """Test parameter validation."""

    def test_ratio_too_high(self):
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            _make_session(auto_commit_threshold_ratio=1.0)

    def test_ratio_too_low(self):
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            _make_session(auto_commit_threshold_ratio=0.0)

    def test_ratio_negative(self):
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            _make_session(auto_commit_threshold_ratio=-0.5)

    def test_ratio_above_one(self):
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            _make_session(auto_commit_threshold_ratio=1.5)

    def test_context_window_zero(self):
        with pytest.raises(ValueError, match="positive integer"):
            _make_session(context_window=0)

    def test_context_window_negative(self):
        with pytest.raises(ValueError, match="positive integer"):
            _make_session(context_window=-100)

    def test_valid_ratio(self):
        """Valid ratio does not raise."""
        session = _make_session(auto_commit_threshold_ratio=0.5, context_window=100000)
        assert session.effective_commit_threshold == 50000
