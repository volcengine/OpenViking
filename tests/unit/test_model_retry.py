# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared model retry helpers."""

import asyncio
import time

import pytest

from openviking.utils.exceptions import AllCredentialsFailedError
from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_CONTENT_SAFETY,
    ERROR_CLASS_INPUT_TOO_LARGE,
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_QUOTA_EXCEEDED,
    ERROR_CLASS_TRANSIENT,
    classify_api_error,
    retry_async,
    retry_deadline_seconds,
    retry_sync,
)


def test_classify_api_error_recognizes_request_burst_too_fast():
    assert classify_api_error(RuntimeError("RequestBurstTooFast")) == ERROR_CLASS_TRANSIENT


def test_classify_all_credentials_failed_prefers_transient_over_auth():
    err = AllCredentialsFailedError(
        [
            ("cred-a", ERROR_CLASS_AUTH, RuntimeError("401 Unauthorized"), 0),
            ("cred-b", ERROR_CLASS_TRANSIENT, RuntimeError("500 server error"), 1),
        ]
    )
    assert classify_api_error(err) == ERROR_CLASS_TRANSIENT


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


def test_auth_takes_precedence_over_quota():
    """Auth errors (e.g. 403) take precedence over the quota substring."""
    assert classify_api_error(RuntimeError("403 AccountQuotaExceeded")) == ERROR_CLASS_AUTH


# --- permanent vs auth split (400 vs 401/403) ---


def test_classify_400_is_permanent():
    """A 400 parameter error is request-level permanent (fail-fast)."""
    error = RuntimeError("Error code: 400 - invalid parameter `model`")
    assert classify_api_error(error) == ERROR_CLASS_PERMANENT


def test_classify_401_is_auth():
    """A 401 is a credential-level auth error (advances in multi-credential mode)."""
    assert classify_api_error(RuntimeError("Error code: 401 - Incorrect API key")) == (
        ERROR_CLASS_AUTH
    )


def test_classify_403_is_auth():
    """A 403 forbidden is a credential-level auth error."""
    assert classify_api_error(RuntimeError("403 forbidden")) == ERROR_CLASS_AUTH


def test_classify_unauthorized_is_auth():
    assert classify_api_error(RuntimeError("Unauthorized")) == ERROR_CLASS_AUTH


def test_classify_account_overdue_is_auth():
    assert classify_api_error(RuntimeError("AccountOverdue")) == ERROR_CLASS_AUTH


# --- content safety classification ---


def test_classify_content_filter_is_content_safety():
    assert classify_api_error(RuntimeError("content_filter triggered")) == (
        ERROR_CLASS_CONTENT_SAFETY
    )


def test_classify_content_policy_is_content_safety():
    error = RuntimeError("The response was rejected by the content policy")
    assert classify_api_error(error) == ERROR_CLASS_CONTENT_SAFETY


def test_content_safety_takes_precedence_over_400():
    """A moderation rejection containing '400' is content_safety, not permanent."""
    error = RuntimeError("Error code: 400 - content_filter: sensitive content detected")
    assert classify_api_error(error) == ERROR_CLASS_CONTENT_SAFETY


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


def test_retry_deadline_seconds_covers_first_call_and_retries():
    assert retry_deadline_seconds(10.0, 3) == 40.0
    assert retry_deadline_seconds(600.0, 0) == 600.0


@pytest.mark.parametrize("attempt_timeout", [0, -1])
def test_retry_deadline_seconds_rejects_non_positive_timeout(attempt_timeout):
    with pytest.raises(ValueError, match="attempt_timeout must be positive"):
        retry_deadline_seconds(attempt_timeout, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1])
async def test_retry_async_rejects_non_positive_timeout(timeout):
    async def _call():
        return "unreachable"

    with pytest.raises(ValueError, match="timeout must be positive"):
        await retry_async(_call, max_retries=0, timeout=timeout)


@pytest.mark.asyncio
async def test_retry_async_cancels_hanging_attempt_when_timeout_expires():
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        await asyncio.sleep(10)
        return "ok"

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="timed out after 0.2s"):
        await retry_async(_call, max_retries=3, timeout=0.2, jitter=False)
    elapsed = time.monotonic() - started

    assert attempts["count"] == 1
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_retry_async_expires_when_a_later_attempt_hangs():
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("Connection error")
        await asyncio.sleep(10)
        return "ok"

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="timed out after 0.3s"):
        await retry_async(
            _call,
            max_retries=3,
            timeout=0.3,
            base_delay=0.01,
            max_delay=0.01,
            jitter=False,
        )
    elapsed = time.monotonic() - started

    assert attempts["count"] == 2
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_retry_async_deadline_includes_backoff_sleep():
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        raise RuntimeError("Connection error")

    with pytest.raises(TimeoutError, match="timed out after 0.1s"):
        await retry_async(
            _call,
            max_retries=3,
            timeout=0.1,
            base_delay=10,
            max_delay=10,
            jitter=False,
        )

    assert attempts["count"] == 1


@pytest.mark.asyncio
async def test_retry_async_omitting_timeout_keeps_success_path():
    async def _call():
        return "ok"

    assert await retry_async(_call, max_retries=3) == "ok"


@pytest.mark.asyncio
async def test_retry_async_without_deadline_retries_inner_timeout_error():
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise asyncio.TimeoutError("timeout while reading provider response")
        return "ok"

    result = await retry_async(
        _call,
        max_retries=1,
        base_delay=0,
        max_delay=0,
        jitter=False,
    )

    assert result == "ok"
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_retry_async_with_deadline_retries_inner_timeout_error():
    attempts = {"count": 0}

    async def _call():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise asyncio.TimeoutError("timeout while reading provider response")
        return "ok"

    result = await retry_async(
        _call,
        max_retries=1,
        timeout=1,
        base_delay=0,
        max_delay=0,
        jitter=False,
    )

    assert result == "ok"
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_retry_async_with_deadline_preserves_inner_timeout_error():
    error = asyncio.TimeoutError("timeout while reading provider response")

    async def _call():
        raise error

    with pytest.raises(asyncio.TimeoutError) as exc_info:
        await retry_async(_call, max_retries=0, timeout=1)

    assert exc_info.value is error


@pytest.mark.asyncio
async def test_retry_async_preserves_caller_cancellation_racing_attempt_completion():
    async def _run_once():
        attempt_started = asyncio.Event()
        release_attempt = asyncio.Event()

        async def _call():
            attempt_started.set()
            await release_attempt.wait()
            return "ok"

        task = asyncio.create_task(retry_async(_call, max_retries=0, timeout=10))
        await attempt_started.wait()

        release_attempt.set()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    for _ in range(20):
        await _run_once()


@pytest.mark.asyncio
async def test_retry_async_preserves_caller_cancellation_during_deadline_cleanup():
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    attempt_finished = asyncio.Event()

    async def _call():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
            return "late"
        finally:
            attempt_finished.set()

    task = asyncio.create_task(retry_async(_call, max_retries=0, timeout=0.01))
    await cleanup_started.wait()

    task.cancel()
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    await attempt_finished.wait()


def test_quota_exceeded_case_insensitive():
    """Quota detection is case-insensitive."""
    assert classify_api_error(RuntimeError("QUOTA LIMIT")) == ERROR_CLASS_QUOTA_EXCEEDED
    assert classify_api_error(RuntimeError("Quota Exceed")) == ERROR_CLASS_QUOTA_EXCEEDED


@pytest.mark.parametrize(
    "message",
    [
        "BadRequestError: 400 maximum context length is 8192 tokens",
        "Error code: 413 - Payload Too Large",
        (
            "Error code: 500 - {'error': {'code': 500, 'message': "
            "'input (8525 tokens) is too large to process. increase the physical batch size "
            "(current batch size: 2048)', 'type': 'server_error'}}"
        ),
    ],
)
def test_classify_input_too_large_errors(message):
    assert classify_api_error(RuntimeError(message)) == ERROR_CLASS_INPUT_TOO_LARGE


def test_retry_sync_does_not_retry_input_too_large():
    attempts = {"count": 0}

    def _call():
        attempts["count"] += 1
        raise RuntimeError("expected maxLength: 50000, actual: 75000")

    with pytest.raises(RuntimeError, match="expected maxLength"):
        retry_sync(_call, max_retries=5)

    assert attempts["count"] == 1


# --- numeric pattern word-boundary tests ---


def test_429_with_request_id_containing_413_is_transient():
    """A 429 error whose request ID happens to contain '413' must NOT be
    misclassified as INPUT_TOO_LARGE (the original bug)."""
    error = RuntimeError(
        "Volcengine hybrid embedding failed: Error code: 429 - "
        "{'error': {'code': 'ModelAccountRpmRateLimitExceeded', "
        "'message': 'RPM limit exceeded', 'param': '', "
        "'type': 'TooManyRequests'}, "
        "'request_id': '0217801248873024288fe53d7c9130f34413480585e683685bc95'}"
    )
    assert classify_api_error(error) == ERROR_CLASS_TRANSIENT


def test_429_with_hyphenated_request_id_containing_413_is_transient():
    """Numeric status codes must not match hyphen-delimited request ID fragments."""
    error = RuntimeError(
        "Volcengine hybrid embedding failed: Error code: 429 - "
        "{'error': {'code': 'ModelAccountRpmRateLimitExceeded', "
        "'message': 'RPM limit exceeded', 'type': 'TooManyRequests'}, "
        "'request_id': 'req-413-abcd'}"
    )
    assert classify_api_error(error) == ERROR_CLASS_TRANSIENT


def test_numeric_status_code_inside_longer_number_is_not_matched():
    """Status code patterns must not match inside longer numbers
    (e.g. '400' must not match '1400')."""
    assert classify_api_error(RuntimeError("status: 1400 OK")) == "unknown"
    assert classify_api_error(RuntimeError("status: 5020 OK")) == "unknown"


def test_numeric_status_code_with_compact_error_code_context_still_matches():
    assert classify_api_error(RuntimeError("Error code:413-Payload Too Large")) == (
        ERROR_CLASS_INPUT_TOO_LARGE
    )
