# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Common exception helpers."""

from openviking.models.model_error import ModelErrorKind

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

    def __init__(
        self,
        errors: list[tuple[str, ModelErrorKind | str, Exception, int]],
    ):
        """Initialize the error with a list of credential failures.

        Args:
            errors: List of tuples containing
                (credential_id, model_error_kind, exception, credential_index)
        """
        self.errors = [
            (
                credential_id,
                kind if isinstance(kind, ModelErrorKind) else ModelErrorKind.UNKNOWN,
                error,
                credential_index,
            )
            for credential_id, kind, error, credential_index in errors
        ]
        kinds = [kind for _, kind, _, _ in self.errors]
        self.kind = self._aggregate_kind(kinds)
        message = "All credentials failed:\n" + "\n".join(
            f"  - {cred_id}: {kind.value} - {exc}" for cred_id, kind, exc, _ in self.errors
        )
        super().__init__(message)

    @staticmethod
    def _aggregate_kind(kinds: list[ModelErrorKind]) -> ModelErrorKind:
        for kind in (
            ModelErrorKind.TRANSIENT_SERVICE_ERROR,
            ModelErrorKind.TRANSIENT_NETWORK_ERROR,
            ModelErrorKind.RATE_LIMIT_ERROR,
            ModelErrorKind.QUOTA_EXHAUSTED,
        ):
            if kind in kinds:
                return kind
        if kinds and all(kind is kinds[0] for kind in kinds):
            return kinds[0]
        return ModelErrorKind.UNKNOWN
