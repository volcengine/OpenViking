# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import threading
import time

import pytest


def test_circuit_breaker_starts_closed():
    from openviking.utils.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(failure_threshold=3, reset_timeout=10)
    cb.check()  # should not raise


def test_circuit_breaker_opens_after_threshold():
    from openviking.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

    cb = CircuitBreaker(failure_threshold=3, reset_timeout=10)
    for _ in range(3):
        cb.record_failure(RuntimeError("500 Internal Server Error"))
    with pytest.raises(CircuitBreakerOpen):
        cb.check()


def test_circuit_breaker_resets_on_success():
    from openviking.utils.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(failure_threshold=3, reset_timeout=10)
    cb.record_failure(RuntimeError("timeout"))
    cb.record_failure(RuntimeError("timeout"))
    cb.record_success()  # resets count
    cb.record_failure(RuntimeError("timeout"))
    cb.record_failure(RuntimeError("timeout"))
    cb.check()  # should not raise — only 2 consecutive failures


def test_circuit_breaker_half_open_after_timeout(monkeypatch):
    from openviking.utils.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(failure_threshold=1, reset_timeout=5)
    cb.record_failure(RuntimeError("500"))
    # Simulate time passing — capture original before patching to avoid recursion
    future = time.monotonic() + 6
    monkeypatch.setattr(time, "monotonic", lambda: future)
    cb.check()  # should not raise — transitions to HALF_OPEN


def test_circuit_breaker_half_open_success_closes():
    from openviking.utils.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(failure_threshold=1, reset_timeout=0)
    cb.record_failure(RuntimeError("500"))
    # reset_timeout=0 means immediate HALF_OPEN
    cb.check()  # transitions to HALF_OPEN
    cb.record_success()  # transitions to CLOSED
    cb.check()  # should not raise


def test_circuit_breaker_half_open_failure_reopens(monkeypatch):
    from openviking.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

    cb = CircuitBreaker(failure_threshold=1, reset_timeout=5)
    cb.record_failure(RuntimeError("500"))
    # Fast-forward past reset_timeout to reach HALF_OPEN
    future = time.monotonic() + 6
    monkeypatch.setattr(time, "monotonic", lambda: future)
    cb.check()  # transitions to HALF_OPEN
    cb.record_failure(RuntimeError("500 again"))
    # Now the breaker is OPEN again, and last_failure_time is `future`,
    # so elapsed is 0 which is < reset_timeout(5) — should raise.
    with pytest.raises(CircuitBreakerOpen):
        cb.check()


def test_half_open_failure_doubles_reset_timeout(monkeypatch):
    from openviking.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

    base = time.monotonic()
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=60, max_reset_timeout=240)
    cb.record_failure(RuntimeError("429 TooManyRequests"))

    monkeypatch.setattr(time, "monotonic", lambda: base + 61)
    cb.check()
    cb.record_failure(RuntimeError("429 TooManyRequests"))

    assert cb._current_reset_timeout == 120

    monkeypatch.setattr(time, "monotonic", lambda: base + 61 + 119)
    with pytest.raises(CircuitBreakerOpen):
        cb.check()


def test_half_open_success_resets_backoff(monkeypatch):
    from openviking.utils.circuit_breaker import CircuitBreaker

    base = time.monotonic()
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=60, max_reset_timeout=240)
    cb.record_failure(RuntimeError("500"))

    monkeypatch.setattr(time, "monotonic", lambda: base + 61)
    cb.check()
    cb.record_failure(RuntimeError("500 again"))
    assert cb._current_reset_timeout == 120

    monkeypatch.setattr(time, "monotonic", lambda: base + 61 + 121)
    cb.check()
    cb.record_success()

    assert cb._current_reset_timeout == 60


def test_permanent_error_trips_immediately():
    from openviking.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

    cb = CircuitBreaker(failure_threshold=10, reset_timeout=10)
    cb.record_failure(RuntimeError("403 Forbidden AccountOverdueError"))
    with pytest.raises(CircuitBreakerOpen):
        cb.check()


def test_retry_after_returns_capped_value():
    from openviking.utils.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(failure_threshold=1, reset_timeout=300)
    cb.record_failure(RuntimeError("500"))
    # retry_after should be capped at 30
    assert 0 < cb.retry_after <= 30


def test_retry_after_zero_when_closed():
    from openviking.utils.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker()
    assert cb.retry_after == 0


def test_thread_safety():
    from openviking.utils.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(failure_threshold=100, reset_timeout=300)
    errors = []

    def record_failures():
        try:
            for _ in range(50):
                cb.record_failure(RuntimeError("500"))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=record_failures) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert cb._failure_count == 200


def test_classify_filesystem_errors_as_permanent():
    from openviking.utils.circuit_breaker import classify_api_error
    from openviking.utils.model_retry import ERROR_CLASS_PERMANENT, ERROR_CLASS_TRANSIENT, ERROR_CLASS_UNKNOWN

    assert classify_api_error(FileNotFoundError("/path/not/found")) == ERROR_CLASS_PERMANENT
    assert classify_api_error(PermissionError("Permission denied")) == ERROR_CLASS_PERMANENT
    assert classify_api_error(IsADirectoryError("Is a directory")) == ERROR_CLASS_PERMANENT
    assert classify_api_error(NotADirectoryError("Not a directory")) == ERROR_CLASS_PERMANENT


def test_classify_chained_filesystem_error_as_permanent():
    from openviking.utils.circuit_breaker import classify_api_error
    from openviking.utils.model_retry import ERROR_CLASS_PERMANENT, ERROR_CLASS_TRANSIENT, ERROR_CLASS_UNKNOWN

    cause = FileNotFoundError("/missing")
    wrapper = RuntimeError("storage layer failed")
    wrapper.__cause__ = cause
    assert classify_api_error(wrapper) == ERROR_CLASS_PERMANENT


def test_classify_permanent_errors():
    from openviking.utils.circuit_breaker import classify_api_error
    from openviking.utils.model_retry import ERROR_CLASS_PERMANENT, ERROR_CLASS_TRANSIENT, ERROR_CLASS_UNKNOWN

    assert classify_api_error(RuntimeError("403 Forbidden")) == ERROR_CLASS_PERMANENT
    assert classify_api_error(RuntimeError("AccountOverdueError: 403")) == ERROR_CLASS_PERMANENT
    assert classify_api_error(RuntimeError("401 Unauthorized")) == ERROR_CLASS_PERMANENT
    assert classify_api_error(RuntimeError("Forbidden")) == ERROR_CLASS_PERMANENT


def test_classify_transient_errors():
    from openviking.utils.circuit_breaker import classify_api_error
    from openviking.utils.model_retry import ERROR_CLASS_PERMANENT, ERROR_CLASS_TRANSIENT, ERROR_CLASS_UNKNOWN

    assert classify_api_error(RuntimeError("429 TooManyRequests")) == ERROR_CLASS_TRANSIENT
    assert classify_api_error(RuntimeError("RateLimitError")) == ERROR_CLASS_TRANSIENT
    assert classify_api_error(RuntimeError("500 Internal Server Error")) == ERROR_CLASS_TRANSIENT
    assert classify_api_error(RuntimeError("502 Bad Gateway")) == ERROR_CLASS_TRANSIENT
    assert classify_api_error(RuntimeError("503 Service Unavailable")) == ERROR_CLASS_TRANSIENT
    assert classify_api_error(RuntimeError("Connection timeout")) == ERROR_CLASS_TRANSIENT
    assert classify_api_error(RuntimeError("ConnectionError: refused")) == ERROR_CLASS_TRANSIENT


def test_classify_unknown_errors():
    from openviking.utils.circuit_breaker import classify_api_error
    from openviking.utils.model_retry import ERROR_CLASS_PERMANENT, ERROR_CLASS_TRANSIENT, ERROR_CLASS_UNKNOWN

    assert classify_api_error(RuntimeError("something unexpected")) == ERROR_CLASS_UNKNOWN
    assert classify_api_error(ValueError("bad value")) == ERROR_CLASS_UNKNOWN


def test_classify_chained_exception():
    from openviking.utils.circuit_breaker import classify_api_error
    from openviking.utils.model_retry import ERROR_CLASS_PERMANENT, ERROR_CLASS_TRANSIENT, ERROR_CLASS_UNKNOWN

    cause = RuntimeError("403 Forbidden")
    wrapper = RuntimeError("API call failed")
    wrapper.__cause__ = cause
    assert classify_api_error(wrapper) == ERROR_CLASS_PERMANENT
