# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Backward compatibility tests for the retry migration.

Verifies that:
- exponential_backoff_retry is still importable from the old location (base.py)
- exponential_backoff_retry signature is unchanged
- exponential_backoff_retry behaviour still works (time-based)
- transient_retry is count-based (different semantics)
"""

from __future__ import annotations

import inspect
import time
from unittest.mock import patch

import pytest


class _HttpError(Exception):
    """Fake HTTP error carrying a numeric status code."""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


class TestExponentialBackoffRetryImportable:

    def test_importable_from_old_location(self):
        """exponential_backoff_retry should still be importable from base.py."""
        from openviking.models.embedder.base import exponential_backoff_retry

        assert callable(exponential_backoff_retry)


class TestExponentialBackoffRetrySignature:

    def test_signature_unchanged(self):
        """exponential_backoff_retry should retain its original signature."""
        from openviking.models.embedder.base import exponential_backoff_retry

        sig = inspect.signature(exponential_backoff_retry)
        param_names = list(sig.parameters.keys())

        expected_params = [
            "func",
            "max_wait",
            "base_delay",
            "max_delay",
            "jitter",
            "is_retryable",
            "logger",
        ]

        assert param_names == expected_params, (
            f"exponential_backoff_retry signature changed.\n"
            f"Expected: {expected_params}\n"
            f"Got:      {param_names}"
        )

    def test_defaults_unchanged(self):
        """Default parameter values should be preserved."""
        from openviking.models.embedder.base import exponential_backoff_retry

        sig = inspect.signature(exponential_backoff_retry)
        params = sig.parameters

        assert params["max_wait"].default == 10.0
        assert params["base_delay"].default == 0.5
        assert params["max_delay"].default == 2.0
        assert params["jitter"].default is True
        assert params["is_retryable"].default is None
        assert params["logger"].default is None


class TestExponentialBackoffRetryBehavior:

    def test_success_first_try(self):
        """Function succeeds on first attempt."""
        from openviking.models.embedder.base import exponential_backoff_retry

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = exponential_backoff_retry(fn)
        assert result == "ok"
        assert call_count == 1

    def test_retries_on_failure(self):
        """Function retries on failure until success."""
        from openviking.models.embedder.base import exponential_backoff_retry

        errors = [Exception("fail"), Exception("fail")]
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return "ok"

        with patch("time.sleep"):
            result = exponential_backoff_retry(fn, max_wait=10.0)

        assert result == "ok"
        assert call_count == 3

    def test_is_time_based(self):
        """exponential_backoff_retry should be time-based (uses max_wait, not count)."""
        from openviking.models.embedder.base import exponential_backoff_retry

        sig = inspect.signature(exponential_backoff_retry)
        param_names = list(sig.parameters.keys())

        # Time-based: has max_wait, no max_retries
        assert "max_wait" in param_names
        assert "max_retries" not in param_names

    def test_respects_is_retryable(self):
        """exponential_backoff_retry should respect is_retryable callback."""
        from openviking.models.embedder.base import exponential_backoff_retry

        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("permanent")

        # is_retryable returns False => no retry
        with patch("time.sleep"):
            with pytest.raises(ValueError):
                exponential_backoff_retry(fn, is_retryable=lambda e: False)

        assert call_count == 1


class TestTransientRetryIsCountBased:

    def test_is_count_based(self):
        """transient_retry should be count-based (uses max_retries, not max_wait)."""
        from openviking.models.retry import transient_retry

        sig = inspect.signature(transient_retry)
        param_names = list(sig.parameters.keys())

        # Count-based: has max_retries, no max_wait
        assert "max_retries" in param_names
        assert "max_wait" not in param_names

    def test_different_from_backoff_retry(self):
        """transient_retry and exponential_backoff_retry should have different signatures."""
        from openviking.models.embedder.base import exponential_backoff_retry
        from openviking.models.retry import transient_retry

        backoff_params = set(inspect.signature(exponential_backoff_retry).parameters.keys())
        retry_params = set(inspect.signature(transient_retry).parameters.keys())

        # They share 'func', 'base_delay', 'max_delay', 'jitter', 'is_retryable'
        # but differ on time vs count control params
        assert "max_wait" in backoff_params
        assert "max_wait" not in retry_params
        assert "max_retries" in retry_params
        assert "max_retries" not in backoff_params
