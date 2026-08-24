# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.observability.http_error_context import (
    MAX_ERROR_CODE_CHARS,
    MAX_ERROR_DETAILS_BYTES,
    MAX_ERROR_MESSAGE_CHARS,
    bind_http_error_context,
    capture_public_http_error,
    get_captured_http_error,
    reset_http_error_context,
    sanitize_public_http_error,
    serialize_public_error_details,
)


def test_public_http_error_is_bounded_and_redacts_credentials() -> None:
    error = sanitize_public_http_error(
        code="X" * 100,
        message=(
            "authorization=Basic dXNlcjpwYXNz, "
            "api_key='top secret', Bearer bearer-secret rejected " + "m" * 600
        ),
        details={
            "api_key": "sk-secret",
            "rejected_value": "Bearer another-secret",
            "nested": {
                "client-secret": "very-secret",
                "message": "password='secret phrase'",
            },
        },
    )

    assert len(error.code) == MAX_ERROR_CODE_CHARS
    assert len(error.message) == MAX_ERROR_MESSAGE_CHARS
    assert "dXNlcjpwYXNz" not in error.message
    assert "top secret" not in error.message
    assert "bearer-secret" not in error.message
    assert error.details == {
        "api_key": "[REDACTED]",
        "rejected_value": "Bearer [REDACTED]",
        "nested": {
            "client-secret": "[REDACTED]",
            "message": "password=[REDACTED]",
        },
    }


def test_oversized_public_error_details_use_bounded_marker() -> None:
    serialized = serialize_public_error_details(
        {f"value-{index}": "x" * 100 for index in range(MAX_ERROR_DETAILS_BYTES // 50)}
    )

    assert serialized == '{"truncated":true}'
    assert len(serialized.encode("utf-8")) <= MAX_ERROR_DETAILS_BYTES


def test_public_error_capture_never_changes_response_control_flow() -> None:
    class BrokenString:
        def __str__(self) -> str:
            raise RuntimeError("cannot serialize")

    token = bind_http_error_context()
    try:
        capture_public_http_error(code=BrokenString(), message="safe")
        assert get_captured_http_error() is None
    finally:
        reset_http_error_context(token)
