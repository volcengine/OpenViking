from __future__ import annotations

from typing import Optional


class OpenVikingError(Exception):
    def __init__(self, message: str, code: str = "UNKNOWN", details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class InvalidArgumentError(OpenVikingError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, code="INVALID_ARGUMENT", details=details)


class InvalidURIError(InvalidArgumentError):
    def __init__(self, uri: str, reason: str = ""):
        message = f"Invalid URI: {uri}"
        if reason:
            message += f" ({reason})"
        super().__init__(message, details={"uri": uri, "reason": reason})
        self.code = "INVALID_URI"


class NotFoundError(OpenVikingError):
    def __init__(self, resource: str, resource_type: str = "resource"):
        details = {"type": resource_type}
        if resource:
            details["resource"] = resource
            message = f"{resource_type.capitalize()} not found: {resource}"
        else:
            message = f"{resource_type.capitalize()} not found"
        super().__init__(message, code="NOT_FOUND", details=details)


class AlreadyExistsError(OpenVikingError):
    def __init__(self, resource: str, resource_type: str = "resource"):
        super().__init__(
            f"{resource_type.capitalize()} already exists: {resource}",
            code="ALREADY_EXISTS",
            details={"resource": resource, "type": resource_type},
        )


class ConflictError(OpenVikingError):
    def __init__(self, message: str, resource: Optional[str] = None):
        super().__init__(
            message, code="CONFLICT", details={"resource": resource} if resource else {}
        )


class FailedPreconditionError(OpenVikingError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, code="FAILED_PRECONDITION", details=details)


class AbortedError(OpenVikingError):
    def __init__(self, message: str = "Operation aborted", details: Optional[dict] = None):
        super().__init__(message, code="ABORTED", details=details)


class UnauthenticatedError(OpenVikingError):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, code="UNAUTHENTICATED")


class PermissionDeniedError(OpenVikingError):
    def __init__(self, message: str = "Permission denied", resource: Optional[str] = None):
        super().__init__(
            message, code="PERMISSION_DENIED", details={"resource": resource} if resource else {}
        )


class UnavailableError(OpenVikingError):
    def __init__(self, service: str = "service", reason: str = ""):
        message = f"{service.capitalize()} unavailable"
        if reason:
            message += f": {reason}"
        super().__init__(
            message, code="UNAVAILABLE", details={"service": service, "reason": reason}
        )


class ResourceExhaustedError(OpenVikingError):
    def __init__(self, message: str = "Resource exhausted", details: Optional[dict] = None):
        super().__init__(message, code="RESOURCE_EXHAUSTED", details=details)


class InternalError(OpenVikingError):
    def __init__(self, message: str = "Internal error", cause: Optional[Exception] = None):
        super().__init__(message, code="INTERNAL", details={"cause": str(cause)} if cause else {})


class DeadlineExceededError(OpenVikingError):
    def __init__(self, operation: str = "operation", timeout: Optional[float] = None):
        message = f"{operation.capitalize()} timed out"
        if timeout:
            message += f" after {timeout}s"
        super().__init__(
            message,
            code="DEADLINE_EXCEEDED",
            details={"operation": operation, "timeout": timeout},
        )


class UnimplementedError(OpenVikingError):
    def __init__(self, message: str = "Operation not implemented", details: Optional[dict] = None):
        super().__init__(message, code="UNIMPLEMENTED", details=details)


class ProcessingError(OpenVikingError):
    def __init__(self, message: str, source: Optional[str] = None):
        super().__init__(
            message, code="PROCESSING_ERROR", details={"source": source} if source else {}
        )


class EmbeddingFailedError(ProcessingError):
    def __init__(self, message: str = "Embedding generation failed", source: Optional[str] = None):
        super().__init__(message, source=source)
        self.code = "EMBEDDING_FAILED"


class VLMFailedError(ProcessingError):
    def __init__(self, message: str = "VLM processing failed", source: Optional[str] = None):
        super().__init__(message, source=source)
        self.code = "VLM_FAILED"


class SessionExpiredError(OpenVikingError):
    def __init__(self, session_id: str):
        super().__init__(
            f"Session expired: {session_id}",
            code="SESSION_EXPIRED",
            details={"session_id": session_id},
        )


class NotInitializedError(OpenVikingError):
    def __init__(self, component: str = "service"):
        super().__init__(
            f"{component.capitalize()} not initialized. Call initialize() first.",
            code="NOT_INITIALIZED",
            details={"component": component},
        )
