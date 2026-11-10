# Copyright (c) 2026 Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.models.model_error import UNKNOWN_BRIEF, ModelErrorKind, brief_for


@pytest.mark.parametrize(
    "kind",
    [
        ModelErrorKind.AUTHENTICATION_ERROR,
        ModelErrorKind.PERMISSION_DENIED,
        ModelErrorKind.QUOTA_EXHAUSTED,
        ModelErrorKind.RATE_LIMIT_ERROR,
        ModelErrorKind.MODEL_NOT_FOUND,
        ModelErrorKind.UNSUPPORTED,
        ModelErrorKind.INVALID_REQUEST,
        ModelErrorKind.INPUT_TOO_LARGE,
        ModelErrorKind.CONTENT_SAFETY,
    ],
)
def test_actionable_model_kinds_keep_dedicated_brief(kind):
    brief = brief_for(kind.value)
    assert brief and brief != UNKNOWN_BRIEF


@pytest.mark.parametrize(
    "kind",
    [ModelErrorKind.TRANSIENT_SERVICE_ERROR, ModelErrorKind.TRANSIENT_NETWORK_ERROR],
)
def test_transient_kinds_collapse_to_unknown(kind):
    assert brief_for(kind.value) == UNKNOWN_BRIEF


def test_unknown_kind_returns_generic():
    assert brief_for(ModelErrorKind.UNKNOWN.value) == UNKNOWN_BRIEF


@pytest.mark.parametrize("bad", [None, "", "vector_db_write", "processing_failed", "nope"])
def test_internal_or_unrecognized_type_returns_none(bad):
    assert brief_for(bad) is None


def _http_429(code: str | None, *, error_type: str | None = None):
    """Build the OpenAI SDK error shape a provider boundary actually raises."""
    import httpx
    import openai

    error: dict[str, object] = {"message": "quota"}
    if code is not None:
        error["code"] = code
    if error_type is not None:
        error["type"] = error_type
    request = httpx.Request("POST", "https://api.example.test/embeddings")
    response = httpx.Response(429, request=request, json={"error": error})
    return openai.APIStatusError("Error code: 429", response=response, body=None)


@pytest.mark.parametrize(
    "code,error_type",
    [
        ("insufficient_quota", "insufficient_quota"),
        ("billing_hard_limit_reached", "billing_hard_limit_reached"),
        ("account_overdue", None),
        ("BillingDisabled", None),
    ],
)
def test_billing_exhaustion_is_terminal(code, error_type):
    """Unfunded / hard-capped billing states never heal on a window roll."""
    from openviking.models.model_error import is_terminal_billing_error

    assert is_terminal_billing_error(_http_429(code, error_type=error_type)) is True


@pytest.mark.parametrize(
    "code",
    [
        # RPM/TPM window throttles.
        "ModelAccountTpmRateLimitExceeded",
        "ModelAccountRpmRateLimitExceeded",
        "RateLimitExceeded",
        # Provider window quota (Gemini reports 429 QUOTA_EXCEEDED as wait-and-retry).
        "QuotaExceeded",
        "AccountQuotaExceeded",
    ],
)
def test_window_throttle_is_not_terminal_billing(code):
    """Window-scoped throttles are transient and must stay re-enqueueable."""
    from openviking.models.model_error import is_terminal_billing_error

    assert is_terminal_billing_error(_http_429(code)) is False


def test_gemini_structured_reason_is_detected():
    """Gemini carries its reason under ``details``, not a top-level code."""
    from openviking.models.model_error import is_terminal_billing_error

    class _GeminiLike(Exception):
        details = [{"reason": "BILLING_HARD_LIMIT_REACHED"}]

    assert is_terminal_billing_error(_GeminiLike()) is True
