# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared model retry helpers."""

from types import SimpleNamespace

import pytest

from openviking.pyagfs.exceptions import (
    AGFSClientError,
    AGFSConnectionError,
    AGFSNotADirectoryError,
    AGFSTimeoutError,
)
from openviking.utils.exceptions import AllCredentialsFailedError
from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_CONTENT_SAFETY,
    ERROR_CLASS_INPUT_TOO_LARGE,
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_QUOTA_EXCEEDED,
    ERROR_CLASS_TRANSIENT,
    ERROR_CLASS_UNKNOWN,
    classify_api_error,
    extract_metric_error_code,
    is_retryable_rate_limit_error,
    retry_async,
    retry_sync,
)


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "error_type",
    [
        FileNotFoundError,
        PermissionError,
        IsADirectoryError,
        NotADirectoryError,
        AGFSNotADirectoryError,
    ],
)
@pytest.mark.parametrize("message", ["/resources/notes.txt", "timeout at /arbitrary/input.md"])
def test_classify_permanent_filesystem_errors(error_type, message, wrapped):
    error = error_type(message)
    if wrapped:
        wrapper = RuntimeError("storage operation failed")
        wrapper.__cause__ = error
        error = wrapper
    assert classify_api_error(error) == ERROR_CLASS_PERMANENT


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AGFSTimeoutError("request timeout"), ERROR_CLASS_TRANSIENT),
        (AGFSConnectionError("connection refused"), ERROR_CLASS_TRANSIENT),
        (AGFSClientError("unexpected storage failure"), ERROR_CLASS_UNKNOWN),
    ],
)
def test_classify_other_agfs_errors(error, expected, wrapped):
    if wrapped:
        wrapper = RuntimeError("storage operation failed")
        wrapper.__cause__ = error
        error = wrapper
    assert classify_api_error(error) == expected


def test_classify_api_error_recognizes_request_burst_too_fast():
    assert classify_api_error(RuntimeError("RequestBurstTooFast")) == ERROR_CLASS_TRANSIENT


@pytest.mark.parametrize("wrapped", [False, True])
def test_terminal_rate_limit_error_is_not_retryable(wrapped):
    error = RuntimeError("Error code: 429 - TooManyRequests")
    error.model_retry_terminal = True
    if wrapped:
        wrapper = RuntimeError("provider call failed")
        wrapper.__cause__ = error
        error = wrapper

    assert not is_retryable_rate_limit_error(error)


class _ProviderError(RuntimeError):
    def __init__(self, *, status_code=None, error_code=None, code=None, body=None, response=None):
        super().__init__("provider request failed")
        self.status_code = status_code
        self.error_code = error_code
        self.code = code
        self.body = body
        self.response = response


def test_extract_metric_error_code_prefers_structured_provider_code():
    assert (
        extract_metric_error_code(_ProviderError(status_code=429, code="RateLimitExceeded"))
        == "429"
    )
    assert extract_metric_error_code(
        _ProviderError(body={"error": {"code": "InvalidParameter"}})
    ) == ("InvalidParameter")
    assert (
        extract_metric_error_code(_ProviderError(response=SimpleNamespace(status_code=503)))
        == "503"
    )


@pytest.mark.parametrize(
    ("status_code", "code", "expected"),
    [
        (400, "AccountOverdue", ERROR_CLASS_AUTH),
        (400, "ContentFilter", ERROR_CLASS_CONTENT_SAFETY),
        (429, "insufficient_quota", ERROR_CLASS_QUOTA_EXCEEDED),
    ],
)
def test_structured_semantic_code_takes_precedence_over_http_status(status_code, code, expected):
    assert classify_api_error(_ProviderError(status_code=status_code, code=code)) == expected


def test_response_status_takes_precedence_over_misleading_message():
    error = RuntimeError("the previous request returned 429 rows")
    error.response = SimpleNamespace(status_code=400)
    assert classify_api_error(error) == ERROR_CLASS_PERMANENT


def test_extract_metric_error_code_uses_safe_fallbacks_only():
    assert extract_metric_error_code(TimeoutError("request timed out")) == "timeout"
    assert extract_metric_error_code(ConnectionError("connection reset")) == "connection_error"
    wrapped_timeout = RuntimeError("request failed")
    wrapped_timeout.__cause__ = TimeoutError("request timed out")
    assert extract_metric_error_code(wrapped_timeout) == "timeout"
    assert extract_metric_error_code(RuntimeError("request_id=not-a-metric-label")) == "unknown"


@pytest.mark.parametrize("wrapped", [False, True])
def test_structured_http_status_takes_precedence_over_conflicting_text(wrapped):
    rate_limited = RuntimeError("Rate limit is 400 requests per minute")
    rate_limited.status_code = 429
    bad_request = RuntimeError("Last request returned 429 rows")
    bad_request.status_code = 400
    if wrapped:
        rate_limit_wrapper = RuntimeError("provider call failed")
        rate_limit_wrapper.__cause__ = rate_limited
        rate_limited = rate_limit_wrapper
        bad_request_wrapper = RuntimeError("provider call failed")
        bad_request_wrapper.__cause__ = bad_request
        bad_request = bad_request_wrapper

    assert classify_api_error(rate_limited) == ERROR_CLASS_TRANSIENT
    assert classify_api_error(bad_request) == ERROR_CLASS_PERMANENT


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


@pytest.mark.parametrize("field", ["code", "type"])
@pytest.mark.parametrize("shape", ["attribute", "body", "nested_body"])
def test_structured_quota_classification_without_message_hint(field, shape):
    error = RuntimeError("TooManyRequests")
    error.status_code = 429
    if shape == "attribute":
        setattr(error, field, "insufficient_quota")
    else:
        body = {field: "insufficient_quota"}
        error.body = {"error": body} if shape == "nested_body" else body
    wrapper = RuntimeError("request failed")
    wrapper.__cause__ = error
    assert classify_api_error(wrapper) == ERROR_CLASS_QUOTA_EXCEEDED


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


def test_quota_exceeded_case_insensitive():
    """Quota detection is case-insensitive."""
    assert classify_api_error(RuntimeError("QUOTA LIMIT")) == ERROR_CLASS_QUOTA_EXCEEDED
    assert classify_api_error(RuntimeError("Quota Exceed")) == ERROR_CLASS_QUOTA_EXCEEDED


@pytest.mark.parametrize(
    "message",
    [
        "BadRequestError: 400 maximum context length is 8192 tokens",
        (
            "Error code: 400 - InvalidParameter: Total tokens of multi-modal content "
            "and text exceed max message tokens."
        ),
        "Error code: 413 - Payload Too Large",
        (
            "Error code: 500 - {'error': {'code': 500, 'message': "
            "'input (8525 tokens) is too large to process. increase the physical batch size "
            "(current batch size: 2048)', 'type': 'server_error'}}"
        ),
        # SiliconFlow generic 400 with structured code 20015, dict-repr form (#4676)
        (
            "Error code: 400 - {'code': 20015, 'message': "
            "'The parameter is invalid. Please check again.', 'data': None}"
        ),
        # JSON string form
        'Error code: 400 - {"code": 20015, "message": "The parameter is invalid."}',
    ],
)
def test_classify_input_too_large_errors(message):
    assert classify_api_error(RuntimeError(message)) == ERROR_CLASS_INPUT_TOO_LARGE


def test_other_siliconflow_400_codes_stay_permanent():
    """Only code 20015 is reclassified; other SiliconFlow parameter errors
    (e.g. a bad model name) must remain permanent request-level failures."""
    error = RuntimeError(
        "Error code: 400 - {'code': 20012, 'message': 'Model not exists', 'data': None}"
    )
    assert classify_api_error(error) == ERROR_CLASS_PERMANENT


def test_20015_inside_request_id_is_not_input_too_large():
    """A rate-limit error whose request ID happens to contain 20015 must not
    be misclassified as INPUT_TOO_LARGE."""
    error = RuntimeError(
        "Error code: 429 - {'error': {'code': 'TooManyRequests', "
        "'message': 'RPM limit exceeded', "
        "'request_id': '0217801248873024200158fe53d7c9130f34413480585e683685bc95'}}"
    )
    assert classify_api_error(error) == ERROR_CLASS_TRANSIENT


@pytest.mark.parametrize(
    "message",
    [
        "expected maxLength: 50000, actual: 75000",
        "Error code: 400 - InvalidParameter: Total tokens of multi-modal content "
        "and text exceed max message tokens.",
    ],
)
def test_input_too_large_does_not_retry_or_trip_breaker(message):
    from openviking.utils.circuit_breaker import CircuitBreaker

    breaker = CircuitBreaker()
    attempts = {"count": 0}

    def _call():
        attempts["count"] += 1
        error = RuntimeError(message)
        breaker.record_failure(error)
        raise error

    with pytest.raises(RuntimeError) as raised:
        retry_sync(_call, max_retries=5)

    assert str(raised.value) == message
    assert attempts["count"] == 1
    breaker.check()


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
