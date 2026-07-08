# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Common exception helpers."""

HTTP_STATUS_TO_ERROR_CODE = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    402: "RESOURCE_EXHAUSTED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    408: "DEADLINE_EXCEEDED",
    409: "CONFLICT",
    422: "INVALID_ARGUMENT",
    429: "RESOURCE_EXHAUSTED",
    500: "UNAVAILABLE",
    502: "UNAVAILABLE",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}


def error_code_from_http_status(status: int | None, default: str = "INVALID_ARGUMENT") -> str:
    if status is None:
        return default
    if status in HTTP_STATUS_TO_ERROR_CODE:
        return HTTP_STATUS_TO_ERROR_CODE[status]
    if 400 <= status < 500:
        return "INVALID_ARGUMENT"
    if 500 <= status < 600:
        return "UNAVAILABLE"
    return default


class AllCredentialsFailedError(Exception):
    """Raised when all credentials in the chain have failed."""

    def __init__(self, errors: list[tuple[str, str, Exception, int]]):
        """Initialize the error with a list of credential failures.

        Args:
            errors: List of tuples containing (credential_id, error_class, exception, attempts)
        """
        self.errors = errors
        message = "All credentials failed:\n" + "\n".join(
            f"  - {cred_id}: {error_class} - {exc}"
            for cred_id, error_class, exc, attempts in errors
        )
        super().__init__(message)
