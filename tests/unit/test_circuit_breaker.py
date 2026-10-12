# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Comprehensive tests for circuit breaker utility."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from openviking.utils.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    classify_api_error,
)
from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_TRANSIENT,
    ERROR_CLASS_UNKNOWN,
)


class TestClassifyApiError:
    """Test API error classification."""

    def test_classify_403_as_auth(self):
        """Test 403 Forbidden is classified as auth (credential-level)."""
        error = Exception("403 Forbidden")
        assert classify_api_error(error) == ERROR_CLASS_AUTH

    def test_classify_401_as_auth(self):
        """Test 401 Unauthorized is classified as auth (credential-level)."""
        error = Exception("401 Unauthorized")
        assert classify_api_error(error) == ERROR_CLASS_AUTH

    def test_classify_forbidden_as_auth(self):
        """Test 'Forbidden' string is classified as auth."""
        error = Exception("Access Forbidden")
        assert classify_api_error(error) == ERROR_CLASS_AUTH

    def test_classify_unauthorized_as_auth(self):
        """Test 'Unauthorized' string is classified as auth."""
        error = Exception("Unauthorized access")
        assert classify_api_error(error) == ERROR_CLASS_AUTH

    def test_classify_account_overdue_as_auth(self):
        """Test 'AccountOverdue' string is classified as auth."""
        error = Exception("AccountOverdue error")
        assert classify_api_error(error) == ERROR_CLASS_AUTH

    def test_classify_429_as_transient(self):
        """Test 429 TooManyRequests is classified as transient."""
        error = Exception("429 TooManyRequests")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_500_as_transient(self):
        """Test 500 Internal Server Error is classified as transient."""
        error = Exception("500 Internal Server Error")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_502_as_transient(self):
        """Test 502 Bad Gateway is classified as transient."""
        error = Exception("502 Bad Gateway")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_503_as_transient(self):
        """Test 503 Service Unavailable is classified as transient."""
        error = Exception("503 Service Unavailable")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_504_as_transient(self):
        """Test 504 Gateway Timeout is classified as transient."""
        error = Exception("504 Gateway Timeout")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_rate_limit_as_transient(self):
        """Test 'RateLimit' string is classified as transient."""
        error = Exception("RateLimit exceeded")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_timeout_as_transient(self):
        """Test 'timeout' string is classified as transient."""
        error = Exception("Connection timeout")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_connection_error_as_transient(self):
        """Test 'ConnectionError' string is classified as transient."""
        error = Exception("ConnectionError: Connection refused")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_connection_refused_as_transient(self):
        """Test 'Connection refused' string is classified as transient."""
        error = Exception("Connection refused by server")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_connection_reset_as_transient(self):
        """Test 'Connection reset' string is classified as transient."""
        error = Exception("Connection reset by peer")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_classify_unknown_error(self):
        """Test unknown error type is classified as unknown."""
        error = Exception("Some random error")
        assert classify_api_error(error) == ERROR_CLASS_UNKNOWN

    def test_classify_error_with_cause(self):
        """Test classification checks error cause."""
        error = Exception("Primary error")
        error.__cause__ = Exception("403 Forbidden")
        assert classify_api_error(error) == ERROR_CLASS_AUTH

    def test_classify_error_with_transient_cause(self):
        """Test classification checks error cause for transient."""
        error = Exception("Primary error")
        error.__cause__ = Exception("429 Rate limit")
        assert classify_api_error(error) == ERROR_CLASS_TRANSIENT

    def test_auth_takes_precedence_over_transient(self):
        """Test auth error patterns are checked before transient."""
        error = Exception("403 Forbidden 429 timeout")
        assert classify_api_error(error) == ERROR_CLASS_AUTH


class TestCircuitBreaker:
    """Test CircuitBreaker class."""

    def test_initial_state_is_closed(self):
        """Test circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker()
        cb.check()  # Should not raise

    def test_record_success_keeps_closed(self):
        """Test recording success keeps breaker closed."""
        cb = CircuitBreaker()
        cb.record_success()
        cb.check()  # Should not raise

    def test_trip_on_threshold(self):
        """Test breaker trips after reaching failure threshold."""
        cb = CircuitBreaker(failure_threshold=3)

        # Record 3 failures
        for _ in range(3):
            cb.record_failure(Exception("500 Error"))

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    def test_no_trip_before_threshold(self):
        """Test breaker doesn't trip before threshold."""
        cb = CircuitBreaker(failure_threshold=5)

        # Record 4 failures (below threshold)
        for _ in range(4):
            cb.record_failure(Exception("500 Error"))

        cb.check()  # Should not raise

    def test_trip_immediately_on_permanent_error(self):
        """Test breaker trips immediately on permanent error."""
        cb = CircuitBreaker(failure_threshold=10)

        # Single permanent error should trip immediately
        cb.record_failure(Exception("403 Forbidden"))

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    def test_input_too_large_does_not_trip_or_increment(self):
        """Test row-specific input errors do not affect global circuit state."""
        cb = CircuitBreaker(failure_threshold=1)

        cb.record_failure(Exception("expected maxLength: 50000, actual: 75000"))

        cb.check()
        assert cb._failure_count == 0

    def test_success_resets_failure_count(self):
        """Test success resets failure count."""
        cb = CircuitBreaker(failure_threshold=3)

        # Record 2 failures
        cb.record_failure(Exception("500 Error"))
        cb.record_failure(Exception("500 Error"))

        # Success should reset count
        cb.record_success()

        # Now 2 more failures shouldn't trip (count reset to 0)
        cb.record_failure(Exception("500 Error"))
        cb.record_failure(Exception("500 Error"))

        cb.check()  # Should not raise

    def test_half_open_allows_probe(self):
        """Test HALF_OPEN state allows probe request."""
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=0.1)

        # Trip the breaker
        cb.record_failure(Exception("500 Error"))

        # Wait for reset timeout
        time.sleep(0.15)

        # Should transition to HALF_OPEN and allow probe
        cb.check()  # Should not raise

    def test_half_open_allows_only_one_concurrent_probe(self):
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=0)
        cb.record_failure(Exception("500 Error"))
        barrier = threading.Barrier(12)

        def check_once():
            barrier.wait()
            try:
                cb.check()
                return True
            except CircuitBreakerOpen:
                return False

        with ThreadPoolExecutor(max_workers=12) as pool:
            admitted = list(pool.map(lambda _: check_once(), range(12)))

        assert admitted.count(True) == 1
        assert admitted.count(False) == 11

    def test_stale_success_cannot_close_a_new_half_open_generation(self):
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=0)
        cb.check()  # lease for an ordinary request in the original generation

        opener = threading.Thread(target=lambda: cb.record_failure(Exception("500 Error")))
        opener.start()
        opener.join()

        probe_admitted = threading.Event()
        finish_probe = threading.Event()

        def probe():
            cb.check()
            probe_admitted.set()
            finish_probe.wait(timeout=2)
            cb.record_success()

        probe_thread = threading.Thread(target=probe)
        probe_thread.start()
        assert probe_admitted.wait(timeout=2)

        cb.record_success()  # late success from the original CLOSED generation
        with pytest.raises(CircuitBreakerOpen, match="probe in progress"):
            cb.check()

        finish_probe.set()
        probe_thread.join(timeout=2)
        assert not probe_thread.is_alive()
        cb.check()

    def test_abandoned_half_open_probe_allows_a_new_probe(self):
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=0)
        cb.record_failure(Exception("500 Error"))

        cb.check()
        cb.abandon()
        cb.check()
        cb.record_success()
        cb.check()

    def test_half_open_to_closed_on_success(self):
        """Test HALF_OPEN transitions to CLOSED on success."""
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=0.1)

        # Trip the breaker
        cb.record_failure(Exception("500 Error"))
        time.sleep(0.15)

        # Transition to HALF_OPEN
        cb.check()

        # Success should close the breaker
        cb.record_success()

        # Should be closed now
        cb.check()  # Should not raise

    def test_half_open_to_open_on_failure(self):
        """Test HALF_OPEN transitions back to OPEN on failure."""
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=0.1)

        # Trip the breaker
        cb.record_failure(Exception("500 Error"))
        time.sleep(0.15)

        # Transition to HALF_OPEN
        cb.check()

        # Failure should reopen
        cb.record_failure(Exception("500 Error"))

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    def test_retry_after_returns_remaining_time(self):
        """Test retry_after returns remaining time when OPEN."""
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=10.0)

        # Trip the breaker
        cb.record_failure(Exception("500 Error"))

        # retry_after should be > 0
        assert cb.retry_after > 0
        assert cb.retry_after <= 30  # Capped at 30s

    def test_retry_after_returns_zero_when_closed(self):
        """Test retry_after returns 0 when CLOSED."""
        cb = CircuitBreaker()
        assert cb.retry_after == 0

    def test_retry_after_capped_at_30(self):
        """Test retry_after is capped at 30 seconds."""
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=1000.0)

        # Trip the breaker
        cb.record_failure(Exception("500 Error"))

        # Should be capped at 30
        assert cb.retry_after == 30

    def test_thread_safety(self):
        """Test circuit breaker is thread-safe."""
        import threading

        cb = CircuitBreaker(failure_threshold=100)
        errors = []

        def worker():
            try:
                for _ in range(100):
                    cb.record_failure(Exception("500 Error"))
                    cb.check()
            except CircuitBreakerOpen:
                errors.append("open")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not have any unexpected errors
        for e in errors:
            assert e == "open"


class TestCircuitBreakerEdgeCases:
    """Test edge cases for circuit breaker."""

    def test_zero_failure_threshold(self):
        """Test with zero failure threshold (trips immediately)."""
        cb = CircuitBreaker(failure_threshold=0)
        # Zero threshold means any failure trips it
        cb.record_failure(Exception("500 Error"))

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    def test_negative_reset_timeout(self):
        """Test with negative reset timeout (immediate reset)."""
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=-1.0)

        cb.record_failure(Exception("500 Error"))

        # With negative timeout, should immediately allow transition
        cb.check()  # Should not raise

    def test_multiple_rapid_failures(self):
        """Test multiple rapid failures."""
        cb = CircuitBreaker(failure_threshold=3)

        for _ in range(10):
            cb.record_failure(Exception("500 Error"))

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    def test_mixed_error_types(self):
        """Test with mixed error types."""
        cb = CircuitBreaker(failure_threshold=5)

        cb.record_failure(Exception("500 Error"))
        cb.record_failure(Exception("429 Rate limit"))
        cb.record_failure(Exception("503 Unavailable"))
        cb.record_failure(Exception("Timeout"))
        cb.record_failure(Exception("Connection reset"))

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    def test_permanent_error_mid_sequence(self):
        """Test permanent error trips breaker regardless of sequence."""
        cb = CircuitBreaker(failure_threshold=10)

        cb.record_failure(Exception("500 Error"))
        cb.record_failure(Exception("500 Error"))
        cb.record_failure(Exception("403 Forbidden"))  # Permanent error
        cb.record_failure(Exception("500 Error"))

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    def test_quota_exceeded_trips_immediately(self):
        """Test breaker trips immediately on quota_exceeded error."""
        cb = CircuitBreaker(failure_threshold=10)

        cb.record_failure(Exception("AccountQuotaExceeded"))

        with pytest.raises(CircuitBreakerOpen):
            cb.check()

    def test_quota_exceeded_trips_even_with_high_threshold(self):
        """Test quota_exceeded bypasses failure threshold."""
        cb = CircuitBreaker(failure_threshold=100)

        cb.record_failure(Exception("usage quota exceeded"))

        with pytest.raises(CircuitBreakerOpen):
            cb.check()


@pytest.mark.parametrize(
    "message", ["400 invalid parameter", "413 payload too large", "content policy rejected"]
)
def test_request_rejection_does_not_change_breaker_health(message):
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure(RuntimeError(message))
    cb.check()
    assert cb._failure_count == 0


@pytest.mark.asyncio
async def test_admission_waits_one_existing_cooldown_then_allows_work(monkeypatch):
    now = [100.0]
    waits = []
    monkeypatch.setattr("openviking.utils.circuit_breaker.time.monotonic", lambda: now[0])

    async def sleep(delay):
        waits.append(delay)
        now[0] += delay

    monkeypatch.setattr("openviking.utils.circuit_breaker.asyncio.sleep", sleep)
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=65)
    cb.record_failure(RuntimeError("503 unavailable"))
    await cb.wait_until_ready()
    assert waits == [30, 30, 5]
    assert cb._failure_count == 1
    cb.record_success()
    cb.check()


@pytest.mark.asyncio
async def test_other_failures_cannot_extend_admission_wait(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("openviking.utils.circuit_breaker.time.monotonic", lambda: now[0])
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=10)
    cb.record_failure(RuntimeError("503 unavailable"))

    async def sleep(delay):
        now[0] += delay
        cb.record_failure(RuntimeError("503 unavailable"))

    monkeypatch.setattr("openviking.utils.circuit_breaker.asyncio.sleep", sleep)
    with pytest.raises(CircuitBreakerOpen, match="after admission wait"):
        await cb.wait_until_ready()
    assert now[0] == 110


@pytest.mark.asyncio
async def test_admission_respects_earlier_absolute_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("openviking.utils.circuit_breaker.time.monotonic", lambda: now[0])
    monkeypatch.setattr("openviking.utils.circuit_breaker.time.time", lambda: now[0] + 900)

    async def sleep(delay):
        now[0] += delay

    monkeypatch.setattr("openviking.utils.circuit_breaker.asyncio.sleep", sleep)
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=60)
    cb.record_failure(RuntimeError("503 unavailable"))
    with pytest.raises(CircuitBreakerOpen, match="deadline"):
        await cb.wait_until_ready(deadline_at=1005)
    assert now[0] == 105


@pytest.mark.asyncio
async def test_admission_wait_is_cancellable_without_changing_health():
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=60)
    cb.record_failure(RuntimeError("503 unavailable"))
    task = asyncio.create_task(cb.wait_until_ready())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cb._failure_count == 1
