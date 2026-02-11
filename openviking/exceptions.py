# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Unified exception classes for OpenViking.

Based on gRPC standard status codes for consistency across service boundaries.
"""

from typing import Optional


class OpenVikingError(Exception):
    """Base exception for all OpenViking errors."""

    def __init__(self, message: str, code: str = "UNKNOWN", details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


# ============= Argument Errors =============


class InvalidArgumentError(OpenVikingError):
    """Invalid argument provided."""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, code="INVALID_ARGUMENT", details=details)


class InvalidURIError(InvalidArgumentError):
    """Invalid Viking URI format."""

    def __init__(self, uri: str, reason: str = ""):
        message = f"Invalid URI: {uri}"
        if reason:
            message += f" ({reason})"
        super().__init__(message, details={"uri": uri, "reason": reason})


class UnsupportedDirectoryFilesError(InvalidArgumentError):
    """Directory contains unsupported file types (used when strict=True)."""

    def __init__(self, message: str, unsupported_files: list):
        super().__init__(message, details={"unsupported_files": unsupported_files})
        self.unsupported_files = unsupported_files


# ============= Resource Errors =============


class NotFoundError(OpenVikingError):
    """Resource not found."""

    def __init__(self, resource: str, resource_type: str = "resource"):
        message = f"{resource_type.capitalize()} not found: {resource}"
        super().__init__(
            message, code="NOT_FOUND", details={"resource": resource, "type": resource_type}
        )


class AlreadyExistsError(OpenVikingError):
    """Resource already exists."""

    def __init__(self, resource: str, resource_type: str = "resource"):
        message = f"{resource_type.capitalize()} already exists: {resource}"
        super().__init__(
            message, code="ALREADY_EXISTS", details={"resource": resource, "type": resource_type}
        )


# ============= Authentication Errors =============


class UnauthenticatedError(OpenVikingError):
    """Authentication required but not provided or invalid."""

    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, code="UNAUTHENTICATED")


class PermissionDeniedError(OpenVikingError):
    """Permission denied for the requested operation."""

    def __init__(self, message: str = "Permission denied", resource: Optional[str] = None):
        details = {"resource": resource} if resource else {}
        super().__init__(message, code="PERMISSION_DENIED", details=details)


# ============= Service Errors =============


class UnavailableError(OpenVikingError):
    """Service temporarily unavailable."""

    def __init__(self, service: str = "service", reason: str = ""):
        message = f"{service.capitalize()} unavailable"
        if reason:
            message += f": {reason}"
        super().__init__(
            message, code="UNAVAILABLE", details={"service": service, "reason": reason}
        )


class InternalError(OpenVikingError):
    """Internal server error."""

    def __init__(self, message: str = "Internal error", cause: Optional[Exception] = None):
        details = {"cause": str(cause)} if cause else {}
        super().__init__(message, code="INTERNAL", details=details)


class DeadlineExceededError(OpenVikingError):
    """Operation timed out."""

    def __init__(self, operation: str = "operation", timeout: Optional[float] = None):
        message = f"{operation.capitalize()} timed out"
        if timeout:
            message += f" after {timeout}s"
        super().__init__(
            message, code="DEADLINE_EXCEEDED", details={"operation": operation, "timeout": timeout}
        )


# ============= Business Errors =============


class ProcessingError(OpenVikingError):
    """Error during resource processing."""

    def __init__(self, message: str, source: Optional[str] = None):
        details = {"source": source} if source else {}
        super().__init__(message, code="PROCESSING_ERROR", details=details)


class EmbeddingFailedError(ProcessingError):
    """Embedding generation failed."""

    def __init__(self, message: str = "Embedding generation failed", source: Optional[str] = None):
        super().__init__(message, source=source)
        self.code = "EMBEDDING_FAILED"


class VLMFailedError(ProcessingError):
    """VLM processing failed."""

    def __init__(self, message: str = "VLM processing failed", source: Optional[str] = None):
        super().__init__(message, source=source)
        self.code = "VLM_FAILED"


class SessionExpiredError(OpenVikingError):
    """Session has expired."""

    def __init__(self, session_id: str):
        message = f"Session expired: {session_id}"
        super().__init__(message, code="SESSION_EXPIRED", details={"session_id": session_id})


class NotInitializedError(OpenVikingError):
    """Service or component not initialized."""

    def __init__(self, component: str = "service"):
        message = f"{component.capitalize()} not initialized. Call initialize() first."
        super().__init__(message, code="NOT_INITIALIZED", details={"component": component})
