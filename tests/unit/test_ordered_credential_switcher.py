# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for OrderedCredentialSwitcher"""

import threading
import time

import pytest

from openviking.utils.model_retry import (
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_QUOTA_EXCEEDED,
    ERROR_CLASS_TRANSIENT,
    ERROR_CLASS_UNKNOWN,
    OrderedCredentialSwitcher,
)


class TestOrderedCredentialSwitcher:
    """Tests for OrderedCredentialSwitcher class."""

    def test_initial_state(self):
        """Test initial state with single credential."""
        switcher = OrderedCredentialSwitcher(n=1)
        assert switcher.n == 1
        assert switcher.get_active_index() == 0
        assert not switcher.is_exhausted

    def test_initial_state_with_multiple_credentials(self):
        """Test initial state with multiple credentials."""
        switcher = OrderedCredentialSwitcher(n=3)
        assert switcher.n == 3
        assert switcher.get_active_index() == 0
        assert not switcher.is_exhausted

    def test_advance_on_quota_exceeded(self):
        """Test advancing to next credential on quota exceeded."""
        switcher = OrderedCredentialSwitcher(n=3)

        # Start at index 0
        assert switcher.get_active_index() == 0

        # Quota exceeded should advance
        result = switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)
        assert result is True
        assert switcher.get_active_index() == 1

        # Another quota exceeded should advance again
        result = switcher.on_failure(1, ERROR_CLASS_QUOTA_EXCEEDED)
        assert result is True
        assert switcher.get_active_index() == 2

        # One more should exhaust all
        result = switcher.on_failure(2, ERROR_CLASS_QUOTA_EXCEEDED)
        assert result is True
        assert switcher.is_exhausted

    def test_fail_fast_on_permanent_error(self):
        """Test that permanent errors cause fail-fast."""
        switcher = OrderedCredentialSwitcher(n=3)

        # Permanent error should NOT advance, return False
        result = switcher.on_failure(0, ERROR_CLASS_PERMANENT)
        assert result is False
        assert switcher.get_active_index() == 0  # Should stay at current index
        assert not switcher.is_exhausted

    def test_transient_error_treated_as_quota(self):
        """Test that transient errors are treated as quota exceeded."""
        switcher = OrderedCredentialSwitcher(n=3)

        # Transient should be treated as quota and advance
        result = switcher.on_failure(0, ERROR_CLASS_TRANSIENT)
        assert result is True
        assert switcher.get_active_index() == 1

    def test_unknown_error_advances(self):
        """Test that unknown errors cause advance."""
        switcher = OrderedCredentialSwitcher(n=3)

        result = switcher.on_failure(0, ERROR_CLASS_UNKNOWN)
        assert result is True
        assert switcher.get_active_index() == 1

    def test_on_success_increments_counter(self):
        """Test that success increments request counter when not at index 0."""
        switcher = OrderedCredentialSwitcher(n=3, failback_request_count=3)

        # Advance to index 1 first
        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)
        assert switcher.get_active_index() == 1

        # Success should increment counter
        switcher.on_success(1)
        switcher.on_success(1)
        switcher.on_success(1)

        # After 3 successes, calling get_active_index should trigger failback
        assert switcher.get_active_index() == 0

    def test_failback_on_request_count(self):
        """Test failback based on request count threshold."""
        switcher = OrderedCredentialSwitcher(n=3, failback_request_count=2)

        # Advance to index 1
        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)
        assert switcher.get_active_index() == 1

        # Two successes should trigger failback
        switcher.on_success(1)
        switcher.on_success(1)

        # Next get_active_index should move back
        assert switcher.get_active_index() == 0

    def test_failback_on_timeout(self):
        """Test failback based on timeout threshold."""
        switcher = OrderedCredentialSwitcher(n=3, failback_timeout_seconds=0.1)

        # Advance to index 1
        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)
        assert switcher.get_active_index() == 1

        # Wait for timeout
        time.sleep(0.2)

        # Next get_active_index should move back
        assert switcher.get_active_index() == 0

    def test_hierarchical_failback_one_step(self):
        """Test that failback moves one step at a time, not all the way."""
        switcher = OrderedCredentialSwitcher(n=4, failback_request_count=2)

        # Advance through indexes
        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)  # 0 -> 1
        switcher.on_failure(1, ERROR_CLASS_QUOTA_EXCEEDED)  # 1 -> 2
        switcher.on_failure(2, ERROR_CLASS_QUOTA_EXCEEDED)  # 2 -> 3
        assert switcher.get_active_index() == 3

        # Two successes should move back to 2
        switcher.on_success(3)
        switcher.on_success(3)
        assert switcher.get_active_index() == 2

        # Two more successes should move back to 1
        switcher.on_success(2)
        switcher.on_success(2)
        assert switcher.get_active_index() == 1

    def test_is_exhausted_when_all_credentials_used(self):
        """Test is_exhausted property."""
        switcher = OrderedCredentialSwitcher(n=2)

        assert not switcher.is_exhausted

        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)  # 0 -> 1
        assert not switcher.is_exhausted

        switcher.on_failure(1, ERROR_CLASS_QUOTA_EXCEEDED)  # 1 -> 2 (exhausted)
        assert switcher.is_exhausted

    def test_success_does_nothing_at_index_zero(self):
        """Test that success at index 0 doesn't increment counter."""
        switcher = OrderedCredentialSwitcher(n=3, failback_request_count=2)

        # Success at index 0 should not affect anything
        switcher.on_success(0)
        switcher.on_success(0)
        switcher.on_success(0)

        # Should still be at index 0
        assert switcher.get_active_index() == 0

    def test_on_success_for_different_index_ignored(self):
        """Test that success for a different index is ignored."""
        switcher = OrderedCredentialSwitcher(n=3, failback_request_count=2)

        # Advance to index 1
        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)
        assert switcher.get_active_index() == 1

        # Success for wrong index should be ignored
        switcher.on_success(0)  # Wrong index
        switcher.on_success(0)  # Wrong index

        # Should still be at index 1 (counter not incremented)
        assert switcher.get_active_index() == 1

    def test_thread_safety(self):
        """Test thread safety of the switcher."""
        switcher = OrderedCredentialSwitcher(n=10)
        results = []

        def worker():
            for _ in range(100):
                idx = switcher.get_active_index()
                if idx < switcher.n - 1:
                    switcher.on_failure(idx, ERROR_CLASS_QUOTA_EXCEEDED)
                switcher.on_success(idx)
                results.append(idx)

        threads = []
        for _ in range(10):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All operations should have completed without errors
        assert len(results) == 1000

    def test_invalid_initialization(self):
        """Test that n < 1 raises ValueError."""
        with pytest.raises(ValueError, match="Number of credentials must be >= 1"):
            OrderedCredentialSwitcher(n=0)

        with pytest.raises(ValueError, match="Number of credentials must be >= 1"):
            OrderedCredentialSwitcher(n=-1)
