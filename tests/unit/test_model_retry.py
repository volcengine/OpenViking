# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared model retry helpers."""

import pytest

from openviking.utils.model_retry import (
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_QUOTA_EXCEEDED,
    ERROR_CLASS_TRANSIENT,
    ERROR_CLASS_UNKNOWN,
    classify_api_error,
    is_quota_exceeded_api_error,
    retry_async,
    retry_sync,
)


def test_classify_api_error_recognizes_request_burst_too_fast():
    assert classify_api_error(RuntimeError("RequestBurstTooFast")) == ERROR_CLASS_TRANSIENT


def test_retry_sync_retries_transient_error_until_success():
    attempts = {"count": 0}

    def _call():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("429 TooManyRequests")
        return "ok"

    assert retry_sync(_call, max_retries=3) == "ok"
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_retry_async_does_not_retry_unknown_error():
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        raise RuntimeError("some unexpected validation failure")

    with pytest.raises(RuntimeError):
        await retry_async(_call, max_retries=3)

    assert attempts["count"] == 1


# --- quota_exceeded classification ---


def test_classify_account_quota_exceeded():
    """AccountQuotaExceeded is classified as quota_exceeded, not transient."""
    error = RuntimeError(
        'API Error: 429 {"error":{"code":"AccountQuotaExceeded",'
        '"message":"You have exceeded the 5-hour usage quota"}}'
    )
    assert classify_api_error(error) == ERROR_CLASS_QUOTA_EXCEEDED


def test_classify_quota_limit():
    """'quota limit' is classified as quota_exceeded."""
    assert classify_api_error(RuntimeError("quota limit reached")) == ERROR_CLASS_QUOTA_EXCEEDED


def test_classify_quota_exceed():
    """'quota exceed' is classified as quota_exceeded."""
    assert classify_api_error(RuntimeError("quota exceed")) == ERROR_CLASS_QUOTA_EXCEEDED


def test_classify_usage_quota():
    """'usage quota' is classified as quota_exceeded."""
    assert classify_api_error(RuntimeError("usage quota exceeded")) == ERROR_CLASS_QUOTA_EXCEEDED


def test_quota_exceeded_takes_precedence_over_transient():
    """A 429 with AccountQuotaExceeded is quota_exceeded, not transient."""
    error = RuntimeError(
        '429 {"error":{"code":"AccountQuotaExceeded","message":"TooManyRequests"}}'
    )
    assert classify_api_error(error) == ERROR_CLASS_QUOTA_EXCEEDED


def test_permanent_still_takes_precedence_over_quota():
    """Permanent errors (e.g. 403) still take highest precedence."""
    assert classify_api_error(RuntimeError("403 AccountQuotaExceeded")) == ERROR_CLASS_PERMANENT


def test_is_quota_exceeded_api_error():
    assert is_quota_exceeded_api_error(RuntimeError("AccountQuotaExceeded")) is True
    assert is_quota_exceeded_api_error(RuntimeError("429 TooManyRequests")) is False


def test_retry_sync_does_not_retry_quota_exceeded():
    """Quota-exceeded errors should NOT be retried."""
    attempts = {"count": 0}

    def _call():
        attempts["count"] += 1
        raise RuntimeError("AccountQuotaExceeded")

    with pytest.raises(RuntimeError, match="AccountQuotaExceeded"):
        retry_sync(_call, max_retries=5)

    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_retry_async_does_not_retry_quota_exceeded():
    """Quota-exceeded errors should NOT be retried (async)."""
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        raise RuntimeError("AccountQuotaExceeded")

    with pytest.raises(RuntimeError, match="AccountQuotaExceeded"):
        await retry_async(_call, max_retries=5)

    assert attempts["count"] == 1


def test_quota_exceeded_case_insensitive():
    """Quota detection is case-insensitive."""
    assert classify_api_error(RuntimeError("QUOTA LIMIT")) == ERROR_CLASS_QUOTA_EXCEEDED
    assert classify_api_error(RuntimeError("Quota Exceed")) == ERROR_CLASS_QUOTA_EXCEEDED
