# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.pyagfs.exceptions import (
    AGFSClientError,
    AGFSConnectionError,
    AGFSHTTPError,
    AGFSTimeoutError,
)
from openviking.storage.errors import ResourceBusyError
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    InvalidURIError,
    NotFoundError,
    OpenVikingError,
    PermissionDeniedError,
    UnavailableError,
)


def is_not_found_error(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, AGFSHTTPError) and exc.status_code == 404:
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "not found",
            "no such file",
            "does not exist",
        )
    )


def is_invalid_uri_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "invalid uri",
            "invalid viking uri",
            "invalid viking://",
        )
    )


def map_exception(
    exc: Exception,
    *,
    resource: str | None = None,
    resource_type: str = "resource",
) -> OpenVikingError | None:
    if isinstance(exc, OpenVikingError):
        return exc
    if isinstance(exc, ResourceBusyError):
        return ConflictError(str(exc), resource=resource)
    if isinstance(exc, PermissionError):
        return PermissionDeniedError(str(exc), resource=resource)
    if isinstance(exc, FileNotFoundError):
        return NotFoundError(resource or str(exc), resource_type)
    if isinstance(exc, ValueError):
        message = str(exc)
        if is_invalid_uri_error(exc):
            return InvalidURIError(resource or message, message)
        if "not a directory" in message.lower():
            details = {"resource": resource} if resource else None
            return FailedPreconditionError(message, details=details)
        return InvalidArgumentError(message, details={"resource": resource} if resource else None)
    if isinstance(exc, (AGFSConnectionError, AGFSTimeoutError)):
        return UnavailableError("storage backend", reason=str(exc))
    if isinstance(exc, AGFSHTTPError):
        if exc.status_code == 404 or is_not_found_error(exc):
            return NotFoundError(resource or str(exc), resource_type)
        if exc.status_code == 403:
            return PermissionDeniedError(str(exc), resource=resource)
        if exc.status_code == 409:
            return ConflictError(str(exc), resource=resource)
        if exc.status_code == 400:
            return InvalidArgumentError(
                str(exc), details={"resource": resource} if resource else None
            )
        if exc.status_code in {500, 502, 503, 504}:
            return UnavailableError("storage backend", reason=str(exc))
    if isinstance(exc, AGFSClientError):
        message = str(exc)
        if is_not_found_error(exc):
            return NotFoundError(resource or message, resource_type)
        if is_invalid_uri_error(exc):
            return InvalidURIError(resource or message, message)
        lowered = message.lower()
        if "permission denied" in lowered:
            return PermissionDeniedError(message, resource=resource)
        if "already exists" in lowered:
            return ConflictError(message, resource=resource)
        if "timeout" in lowered or "connection refused" in lowered:
            return UnavailableError("storage backend", reason=message)
    message = str(exc)
    lowered = message.lower()
    if is_not_found_error(exc):
        return NotFoundError(resource or message, resource_type)
    if is_invalid_uri_error(exc):
        return InvalidURIError(resource or message, message)
    if "permission denied" in lowered or "access denied" in lowered:
        return PermissionDeniedError(message, resource=resource)
    if "timeout" in lowered or "connection refused" in lowered:
        return UnavailableError("storage backend", reason=message)
    return None
