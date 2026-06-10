# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for OrderedCredentialSwitcher"""

import threading
import time

import pytest

from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_CONTENT_SAFETY,
    ERROR_CLASS_INPUT_TOO_LARGE,
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
        """PERMANENT (400 parameter error) always fails fast, even with multiple credentials.

        A 400 is request-level: the same request fails on every credential of
        the same model, so switching is useless.
        """
        switcher = OrderedCredentialSwitcher(n=3)

        result = switcher.on_failure(0, ERROR_CLASS_PERMANENT)
        assert result is False
        assert switcher.get_active_index() == 0  # Should stay at current index
        assert not switcher.is_exhausted

    def test_fail_fast_on_input_too_large(self):
        """INPUT_TOO_LARGE fails fast: same oversized input fails on every credential."""
        switcher = OrderedCredentialSwitcher(n=3)

        result = switcher.on_failure(0, ERROR_CLASS_INPUT_TOO_LARGE)
        assert result is False
        assert switcher.get_active_index() == 0
        assert not switcher.is_exhausted

    def test_fail_fast_on_content_safety(self):
        """CONTENT_SAFETY fails fast: moderation rejects the content on every credential."""
        switcher = OrderedCredentialSwitcher(n=3)

        result = switcher.on_failure(0, ERROR_CLASS_CONTENT_SAFETY)
        assert result is False
        assert switcher.get_active_index() == 0
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

        # get_active_index is a pure read and must NOT trigger failback
        assert switcher.get_active_index() == 1
        # After 3 successes, maybe_failback should move back to index 0
        assert switcher.maybe_failback() == 0

    def test_failback_on_request_count(self):
        """Test failback based on request count threshold."""
        switcher = OrderedCredentialSwitcher(n=3, failback_request_count=2)

        # Advance to index 1
        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)
        assert switcher.get_active_index() == 1

        # Two successes should trigger failback
        switcher.on_success(1)
        switcher.on_success(1)

        # Next maybe_failback should move back
        assert switcher.maybe_failback() == 0

    def test_failback_on_timeout(self):
        """Test failback based on timeout threshold."""
        switcher = OrderedCredentialSwitcher(n=3, failback_timeout_seconds=0.1)

        # Advance to index 1
        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)
        assert switcher.get_active_index() == 1

        # Wait for timeout
        time.sleep(0.2)

        # Next maybe_failback should move back
        assert switcher.maybe_failback() == 0

    def test_get_active_index_is_pure_read(self):
        """get_active_index must never trigger failback, even when thresholds are met.

        Observers (logging/metrics reading active_credential_id/index) must not
        accidentally advance the failback state machine.
        """
        switcher = OrderedCredentialSwitcher(
            n=3, failback_request_count=1, failback_timeout_seconds=0.05
        )

        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)
        assert switcher.get_active_index() == 1

        # Meet BOTH thresholds (count + timeout)
        switcher.on_success(1)
        time.sleep(0.1)

        # Repeated pure reads must stay at index 1 (no failback side effect)
        assert switcher.get_active_index() == 1
        assert switcher.get_active_index() == 1

        # Only maybe_failback actually moves back
        assert switcher.maybe_failback() == 0

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
        assert switcher.maybe_failback() == 2

        # Two more successes should move back to 1
        switcher.on_success(2)
        switcher.on_success(2)
        assert switcher.maybe_failback() == 1

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

    def test_advance_on_auth_error_when_multi_credential(self):
        """Multi-credential mode: AUTH (401/403) advances to next credential by default."""
        switcher = OrderedCredentialSwitcher(n=3)

        # First credential's auth error should advance, not fail-fast
        result = switcher.on_failure(0, ERROR_CLASS_AUTH)
        assert result is True
        assert switcher.get_active_index() == 1

        # Middle credential's auth error should also advance
        result = switcher.on_failure(1, ERROR_CLASS_AUTH)
        assert result is True
        assert switcher.get_active_index() == 2

    def test_last_credential_auth_error_still_fails_fast(self):
        """In multi-credential mode the last credential still fails fast on AUTH."""
        switcher = OrderedCredentialSwitcher(n=2)

        # Advance from 0 -> 1 on auth error
        assert switcher.on_failure(0, ERROR_CLASS_AUTH) is True
        assert switcher.get_active_index() == 1

        # On the last credential (idx 1, n=2), still fail-fast
        result = switcher.on_failure(1, ERROR_CLASS_AUTH)
        assert result is False
        # active_idx stays at 1 (not bumped to n=2/exhausted) so caller can raise normally
        assert switcher.get_active_index() == 1
        assert not switcher.is_exhausted

    def test_single_credential_auth_error_fails_fast(self):
        """With only one credential, AUTH must fail-fast (it's the last)."""
        switcher = OrderedCredentialSwitcher(n=1)

        result = switcher.on_failure(0, ERROR_CLASS_AUTH)
        assert result is False
        assert switcher.get_active_index() == 0

    def test_is_fail_fast_classification(self):
        """Request-level errors are fail-fast; credential/quota/transient are not."""
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_PERMANENT) is True
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_INPUT_TOO_LARGE) is True
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_CONTENT_SAFETY) is True
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_AUTH) is False
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_QUOTA_EXCEEDED) is False
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_TRANSIENT) is False
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_UNKNOWN) is False

    def test_commit_success_same_index_increments_failback_counter(self):
        """commit_success on the active (non-zero) index advances failback counter."""
        switcher = OrderedCredentialSwitcher(n=3, failback_request_count=2)

        switcher.on_failure(0, ERROR_CLASS_QUOTA_EXCEEDED)  # active -> 1
        assert switcher.get_active_index() == 1

        switcher.commit_success(1)
        switcher.commit_success(1)
        # Two successes at the active index satisfy the failback threshold.
        assert switcher.maybe_failback() == 0

    def test_commit_success_different_index_fast_failover(self):
        """commit_success on a different index commits it as the new active one."""
        switcher = OrderedCredentialSwitcher(n=3)

        # active starts at 0; a later credential (idx 2) served the request.
        switcher.commit_success(2)
        assert switcher.get_active_index() == 2

    def test_commit_success_at_index_zero_no_counter(self):
        """commit_success at index 0 keeps active at 0 and does not count."""
        switcher = OrderedCredentialSwitcher(n=3, failback_request_count=1)

        switcher.commit_success(0)
        switcher.commit_success(0)
        assert switcher.get_active_index() == 0
        # Still at 0, nothing to fail back to.
        assert switcher.maybe_failback() == 0
