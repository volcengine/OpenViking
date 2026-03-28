# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Comprehensive tests for the core retry module (openviking.models.retry).

Tests cover:
- is_transient_error: ~28 parametrized cases (14 transient, 14 permanent)
- transient_retry (sync): 8 behavioral tests
- transient_retry_async (async): 8 mirrored behavioral tests
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from openviking.models.retry import is_transient_error, transient_retry, transient_retry_async

# ---------------------------------------------------------------------------
# Helper fake HTTP error with status_code attribute
# ---------------------------------------------------------------------------


class _HttpError(Exception):
    """Fake HTTP error carrying a numeric status code for testing."""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


# ---------------------------------------------------------------------------
# is_transient_error — parametrized cases
# ---------------------------------------------------------------------------

_TRANSIENT_CASES = [
    # HTTP status codes via _HttpError.status_code
    pytest.param(_HttpError(429), True, id="http_429"),
    pytest.param(_HttpError(500), True, id="http_500"),
    pytest.param(_HttpError(502), True, id="http_502"),
    pytest.param(_HttpError(503), True, id="http_503"),
    pytest.param(_HttpError(504), True, id="http_504"),
    # Built-in connection exceptions
    pytest.param(ConnectionError("connection failed"), True, id="ConnectionError"),
    pytest.param(ConnectionResetError("reset"), True, id="ConnectionResetError"),
    pytest.param(ConnectionRefusedError("refused"), True, id="ConnectionRefusedError"),
    pytest.param(TimeoutError("timed out"), True, id="TimeoutError"),
    pytest.param(asyncio.TimeoutError(), True, id="asyncio_TimeoutError"),
    # String-pattern transient errors
    pytest.param(Exception("TooManyRequests from server"), True, id="str_TooManyRequests"),
    pytest.param(Exception("RateLimit exceeded"), True, id="str_RateLimit"),
    pytest.param(Exception("RequestBurstTooFast"), True, id="str_RequestBurstTooFast"),
    pytest.param(Exception("request timed out after 30s"), True, id="str_timed_out"),
]

_PERMANENT_CASES = [
    # HTTP status codes via _HttpError.status_code
    pytest.param(_HttpError(400), False, id="http_400"),
    pytest.param(_HttpError(401), False, id="http_401"),
    pytest.param(_HttpError(403), False, id="http_403"),
    pytest.param(_HttpError(404), False, id="http_404"),
    pytest.param(_HttpError(422), False, id="http_422"),
    # Built-in value/type errors
    pytest.param(ValueError("bad value"), False, id="ValueError"),
    pytest.param(TypeError("wrong type"), False, id="TypeError"),
    # String-pattern permanent errors
    pytest.param(
        Exception("InvalidRequestError: field missing"), False, id="str_InvalidRequestError"
    ),
    pytest.param(
        Exception("AuthenticationError: invalid key"), False, id="str_AuthenticationError"
    ),
    # Unknown errors — conservative default False
    pytest.param(Exception("some unknown error"), False, id="unknown_generic"),
    pytest.param(RuntimeError("unexpected state"), False, id="RuntimeError_unknown"),
    pytest.param(KeyError("missing key"), False, id="KeyError"),
    pytest.param(AttributeError("no attr"), False, id="AttributeError"),
    pytest.param(
        Exception("config_value_out_of_range"), False, id="str_unknown_no_transient_keyword"
    ),
]


@pytest.mark.parametrize("exc,expected", _TRANSIENT_CASES)
def test_is_transient_error_transient(exc, expected):
    """Transient errors should be classified as retryable (True)."""
    assert is_transient_error(exc) is expected


@pytest.mark.parametrize("exc,expected", _PERMANENT_CASES)
def test_is_transient_error_permanent(exc, expected):
    """Permanent / unknown errors should not be retried (False)."""
    assert is_transient_error(exc) is expected


# ---------------------------------------------------------------------------
# transient_retry (sync)
# ---------------------------------------------------------------------------


class TestTransientRetrySync:
    """Sync retry behaviour tests."""

    def test_success_first_try(self):
        """Function succeeds on first attempt — call_count == 1."""
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = transient_retry(fn, max_retries=3)
        assert result == "ok"
        assert call_count == 1

    def test_retry_then_success(self):
        """Two transient failures then success — call_count == 3."""
        errors = [_HttpError(429), _HttpError(503)]
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return "ok"

        with patch("time.sleep"):
            result = transient_retry(fn, max_retries=3)

        assert result == "ok"
        assert call_count == 3

    def test_permanent_error_no_retry(self):
        """Permanent error (401) should not be retried — call_count == 1 and raises."""
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise _HttpError(401)

        with patch("time.sleep"):
            with pytest.raises(_HttpError):
                transient_retry(fn, max_retries=3)

        assert call_count == 1

    def test_max_retries_exhausted(self):
        """4 consecutive 429 errors with max_retries=3 → raises after 4 calls."""
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise _HttpError(429)

        with patch("time.sleep"):
            with pytest.raises(_HttpError):
                transient_retry(fn, max_retries=3)

        assert call_count == 4  # 1 initial + 3 retries

    def test_max_retries_zero_raises_immediately(self):
        """max_retries=0 disables retrying — call_count == 1."""
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise _HttpError(429)

        with patch("time.sleep"):
            with pytest.raises(_HttpError):
                transient_retry(fn, max_retries=0)

        assert call_count == 1

    def test_max_retries_one(self):
        """max_retries=1: one failure then success → call_count == 2."""
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _HttpError(429)
            return "done"

        with patch("time.sleep"):
            result = transient_retry(fn, max_retries=1)

        assert result == "done"
        assert call_count == 2

    def test_backoff_delays_exponential(self):
        """Verify exponential backoff: base_delay=1.0, jitter=False → 1.0, 2.0, 4.0."""
        call_count = 0
        sleep_calls = []

        def fn():
            nonlocal call_count
            call_count += 1
            raise _HttpError(429)

        with patch("time.sleep", side_effect=lambda d: sleep_calls.append(d)):
            with pytest.raises(_HttpError):
                transient_retry(fn, max_retries=3, base_delay=1.0, max_delay=100.0, jitter=False)

        assert len(sleep_calls) == 3
        assert sleep_calls[0] == pytest.approx(1.0)
        assert sleep_calls[1] == pytest.approx(2.0)
        assert sleep_calls[2] == pytest.approx(4.0)

    def test_delay_capped_at_max_delay(self):
        """Delays must not exceed max_delay even with many retries."""
        sleep_calls = []
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise _HttpError(503)

        with patch("time.sleep", side_effect=lambda d: sleep_calls.append(d)):
            with pytest.raises(_HttpError):
                transient_retry(fn, max_retries=10, base_delay=1.0, max_delay=8.0, jitter=False)

        assert all(d <= 8.0 for d in sleep_calls), f"Some delays exceed max_delay: {sleep_calls}"


# ---------------------------------------------------------------------------
# transient_retry_async (async)
# ---------------------------------------------------------------------------


class TestTransientRetryAsync:
    """Async retry behaviour tests — mirrors sync suite."""

    async def test_success_first_try(self):
        """Async function succeeds on first attempt — call_count == 1."""
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await transient_retry_async(coro, max_retries=3)
        assert result == "ok"
        assert call_count == 1

    async def test_retry_then_success(self):
        """Two transient failures then success — call_count == 3."""
        errors = [_HttpError(429), _HttpError(503)]
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            if errors:
                raise errors.pop(0)
            return "ok"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await transient_retry_async(coro, max_retries=3)

        assert result == "ok"
        assert call_count == 3

    async def test_permanent_error_no_retry(self):
        """Permanent error (401) should not be retried — call_count == 1 and raises."""
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            raise _HttpError(401)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(_HttpError):
                await transient_retry_async(coro, max_retries=3)

        assert call_count == 1

    async def test_max_retries_exhausted(self):
        """4 consecutive 429 errors with max_retries=3 → raises after 4 calls."""
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            raise _HttpError(429)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(_HttpError):
                await transient_retry_async(coro, max_retries=3)

        assert call_count == 4

    async def test_max_retries_zero_raises_immediately(self):
        """max_retries=0 disables retrying — call_count == 1."""
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            raise _HttpError(429)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(_HttpError):
                await transient_retry_async(coro, max_retries=0)

        assert call_count == 1

    async def test_max_retries_one(self):
        """max_retries=1: one failure then success → call_count == 2."""
        call_count = 0

        async def coro():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _HttpError(429)
            return "done"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await transient_retry_async(coro, max_retries=1)

        assert result == "done"
        assert call_count == 2

    async def test_backoff_delays_exponential(self):
        """Verify exponential backoff: base_delay=1.0, jitter=False → 1.0, 2.0, 4.0."""
        call_count = 0
        sleep_calls = []

        async def fake_sleep(d):
            sleep_calls.append(d)

        async def coro():
            nonlocal call_count
            call_count += 1
            raise _HttpError(429)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(_HttpError):
                await transient_retry_async(
                    coro, max_retries=3, base_delay=1.0, max_delay=100.0, jitter=False
                )

        assert len(sleep_calls) == 3
        assert sleep_calls[0] == pytest.approx(1.0)
        assert sleep_calls[1] == pytest.approx(2.0)
        assert sleep_calls[2] == pytest.approx(4.0)

    async def test_delay_capped_at_max_delay(self):
        """Async delays must not exceed max_delay even with many retries."""
        sleep_calls = []
        call_count = 0

        async def fake_sleep(d):
            sleep_calls.append(d)

        async def coro():
            nonlocal call_count
            call_count += 1
            raise _HttpError(503)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(_HttpError):
                await transient_retry_async(
                    coro, max_retries=10, base_delay=1.0, max_delay=8.0, jitter=False
                )

        assert all(d <= 8.0 for d in sleep_calls), f"Some delays exceed max_delay: {sleep_calls}"


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


class TestIsTransientErrorEdgeCases:
    """Edge cases for is_transient_error."""

    def test_timeout_substring_in_message(self):
        """'timeout' substring in message → transient."""
        err = Exception("connection timeout after 10s")
        assert is_transient_error(err) is True

    def test_status_code_attribute_takes_priority(self):
        """status_code=503 → transient, even if message says 'bad request'."""
        err = _HttpError(503, "bad request")
        assert is_transient_error(err) is True

    def test_status_code_401_permanent_priority(self):
        """status_code=401 → permanent, even if message contains 'timeout'."""
        err = _HttpError(401, "timeout auth failure")
        assert is_transient_error(err) is False

    def test_custom_is_retryable_overrides(self):
        """Custom is_retryable callback overrides default classification."""
        # 429 is normally transient but we pass a custom fn that returns False
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise _HttpError(429)

        with patch("time.sleep"):
            with pytest.raises(_HttpError):
                transient_retry(fn, max_retries=3, is_retryable=lambda e: False)

        assert call_count == 1  # no retries because custom fn says not retryable

    def test_http_status_attribute_variant(self):
        """Objects with .http_status should be checked for transient status."""

        class AltHttpError(Exception):
            def __init__(self, http_status: int):
                super().__init__(f"HTTP {http_status}")
                self.http_status = http_status

        assert is_transient_error(AltHttpError(503)) is True
        assert is_transient_error(AltHttpError(401)) is False

    def test_code_attribute_variant(self):
        """Objects with .code should be checked for transient status."""

        class CodeError(Exception):
            def __init__(self, code: int):
                super().__init__(f"Error code {code}")
                self.code = code

        assert is_transient_error(CodeError(429)) is True
        assert is_transient_error(CodeError(403)) is False
