# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for OrderedCredentialSwitcher"""


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

    def test_success_does_nothing_at_index_zero(self):
        """Test that success at index 0 doesn't increment counter."""
        switcher = OrderedCredentialSwitcher(n=3, failback_request_count=2)

        # Success at index 0 should not affect anything
        switcher.on_success(0)
        switcher.on_success(0)
        switcher.on_success(0)

        # Should still be at index 0
        assert switcher.get_active_index() == 0

    def test_invalid_initialization(self):
        """Test that n < 1 raises ValueError."""
        with pytest.raises(ValueError, match="Number of credentials must be >= 1"):
            OrderedCredentialSwitcher(n=0)

        with pytest.raises(ValueError, match="Number of credentials must be >= 1"):
            OrderedCredentialSwitcher(n=-1)

    def test_is_fail_fast_classification(self):
        """Request-level errors are fail-fast; credential/quota/transient are not."""
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_PERMANENT) is True
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_INPUT_TOO_LARGE) is True
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_CONTENT_SAFETY) is True
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_AUTH) is False
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_QUOTA_EXCEEDED) is False
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_TRANSIENT) is False
        assert OrderedCredentialSwitcher.is_fail_fast(ERROR_CLASS_UNKNOWN) is False

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
