# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Common exception helpers."""

from openviking.models.model_error import (
    ACCOUNT_RATE_LIMIT_CODE,
    MODEL_RATE_LIMIT_MESSAGE,
    ModelError,
    ModelErrorKind,
    exception_chain,
    model_error_from_exception,
    model_error_kind,
)
from openviking_cli.exceptions import OpenVikingError, ResourceExhaustedError

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

    @property
    def common_kind(self) -> ModelErrorKind:
        """Only report a single cause when every credential failed the same way."""
        kinds = {kind for _, kind, _, _ in self.errors}
        return kinds.pop() if len(kinds) == 1 else ModelErrorKind.UNKNOWN

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


def reported_model_error_kind(exc: Exception) -> ModelErrorKind | None:
    for cause in exception_chain(exc):
        if isinstance(cause, AllCredentialsFailedError):
            return cause.common_kind
    return model_error_kind(exc)


class ModelRateLimitError(ResourceExhaustedError):
    """A public RESOURCE_EXHAUSTED error originating at the model provider."""

    def __init__(self, details: dict | None = None) -> None:
        details = {**(details or {}), "model_error_kind": ModelErrorKind.RATE_LIMIT_ERROR.value}
        message = MODEL_RATE_LIMIT_MESSAGE
        if details.get("upstream_error_code") == ACCOUNT_RATE_LIMIT_CODE:
            message = "The upstream model provider's account rate limit was exceeded. Please try again later."
        super().__init__(message, details=details)


def model_rate_limit_error(exc: Exception) -> ModelRateLimitError | None:
    """Classify a provider failure before an HTTP response or task serializes it."""
    for cause in exception_chain(exc):
        if isinstance(cause, ModelRateLimitError):
            return cause
        if isinstance(cause, OpenVikingError):
            return None
        if isinstance(cause, AllCredentialsFailedError):
            # The retry policy's aggregate kind can represent just one credential.
            if cause.common_kind is ModelErrorKind.RATE_LIMIT_ERROR:
                return ModelRateLimitError({"upstream_error_type": type(cause).__name__})
            return None
        if isinstance(cause, ModelError):
            model_error = cause
            break
    else:
        model_error = model_error_from_exception(exc)
        # A bare HTTP client error could have come from storage or a connector.
        if model_error is None or model_error.provider in {"http", "network"}:
            return None
        cause = exc

    if model_error.kind is not ModelErrorKind.RATE_LIMIT_ERROR:
        return None
    details: dict = {"upstream_error_type": type(cause).__name__}
    if model_error.provider:
        details["provider"] = model_error.provider
    if model_error.code:
        details["upstream_error_code"] = model_error.code
    if model_error.status_code is not None:
        details["upstream_status_code"] = model_error.status_code
    return ModelRateLimitError(details)
