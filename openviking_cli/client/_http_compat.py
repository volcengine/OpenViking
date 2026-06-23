# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compatibility bridge for legacy HTTP client entry points."""

from __future__ import annotations

from typing import Any, Dict

from openviking_cli._sdk_import import import_openviking_sdk
from openviking_cli.exceptions import (
    AbortedError,
    AlreadyExistsError,
    ConflictError,
    DeadlineExceededError,
    EmbeddingFailedError,
    FailedPreconditionError,
    InternalError,
    InvalidArgumentError,
    InvalidURIError,
    NotFoundError,
    NotInitializedError,
    OpenVikingError,
    PermissionDeniedError,
    ProcessingError,
    ResourceExhaustedError,
    SessionExpiredError,
    UnauthenticatedError,
    UnavailableError,
    UnimplementedError,
    VLMFailedError,
)

ERROR_CODE_TO_EXCEPTION = {
    "INVALID_ARGUMENT": InvalidArgumentError,
    "INVALID_URI": InvalidURIError,
    "NOT_FOUND": NotFoundError,
    "ALREADY_EXISTS": AlreadyExistsError,
    "CONFLICT": ConflictError,
    "FAILED_PRECONDITION": FailedPreconditionError,
    "ABORTED": AbortedError,
    "UNAUTHENTICATED": UnauthenticatedError,
    "PERMISSION_DENIED": PermissionDeniedError,
    "RESOURCE_EXHAUSTED": ResourceExhaustedError,
    "UNAVAILABLE": UnavailableError,
    "INTERNAL": InternalError,
    "DEADLINE_EXCEEDED": DeadlineExceededError,
    "UNIMPLEMENTED": UnimplementedError,
    "NOT_INITIALIZED": NotInitializedError,
    "PROCESSING_ERROR": ProcessingError,
    "EMBEDDING_FAILED": EmbeddingFailedError,
    "VLM_FAILED": VLMFailedError,
    "SESSION_EXPIRED": SessionExpiredError,
    "UNKNOWN": OpenVikingError,
}


def _raise_legacy_exception(error: Dict[str, Any]) -> None:
    code = error.get("code", "UNKNOWN")
    message = error.get("message", "Unknown error")
    details = error.get("details")
    exc_class = ERROR_CODE_TO_EXCEPTION.get(code, OpenVikingError)

    if exc_class == OpenVikingError:
        raise exc_class(message, code=code, details=details)
    if exc_class in (
        InvalidArgumentError,
        FailedPreconditionError,
        ResourceExhaustedError,
        AbortedError,
        UnimplementedError,
    ):
        raise exc_class(message, details=details)
    if exc_class == InvalidURIError:
        uri = details.get("uri", "") if details else ""
        reason = details.get("reason", "") if details else ""
        raise exc_class(uri, reason)
    if exc_class == NotFoundError:
        resource = details.get("resource", "") if details else ""
        resource_type = details.get("type", "resource") if details else "resource"
        raise exc_class(resource, resource_type)
    if exc_class == AlreadyExistsError:
        resource = details.get("resource", "") if details else ""
        resource_type = details.get("type", "resource") if details else "resource"
        raise exc_class(resource, resource_type)
    raise exc_class(message)


class AsyncHTTPClient(import_openviking_sdk().AsyncHTTPClient):
    def _raise_exception(self, error: Dict[str, Any]) -> None:
        _raise_legacy_exception(error)


class SyncHTTPClient(import_openviking_sdk().SyncHTTPClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._async_client = AsyncHTTPClient(*args, **kwargs)
