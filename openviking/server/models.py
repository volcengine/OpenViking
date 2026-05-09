# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models and error codes for OpenViking HTTP Server."""

from typing import Any, Dict, Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorInfo(BaseModel):
    """Error information."""

    code: str
    message: str
    details: Optional[dict] = None


class Response(BaseModel, Generic[T]):
    """Standard API response.

    Use ``Response[SomeModel]`` in route signatures to get a fully typed
    OpenAPI schema. When the generic parameter is omitted the model is
    equivalent to ``Response[Any]`` (preserves historical behavior).
    """

    status: str  # "ok" | "error"
    result: Optional[T] = None
    error: Optional[ErrorInfo] = None
    telemetry: Optional[Dict[str, Any]] = None


# Error code to HTTP status code mapping
ERROR_CODE_TO_HTTP_STATUS = {
    "OK": 200,
    "INVALID_ARGUMENT": 400,
    "INVALID_URI": 400,
    "NOT_FOUND": 404,
    "ALREADY_EXISTS": 409,
    "CONFLICT": 409,
    "PERMISSION_DENIED": 403,
    "UNAUTHENTICATED": 401,
    "RESOURCE_EXHAUSTED": 429,
    "FAILED_PRECONDITION": 412,
    "ABORTED": 409,
    "DEADLINE_EXCEEDED": 504,
    "UNAVAILABLE": 503,
    "INTERNAL": 500,
    "UNIMPLEMENTED": 501,
    "NOT_INITIALIZED": 500,
    "PROCESSING_ERROR": 500,
    "EMBEDDING_FAILED": 500,
    "VLM_FAILED": 500,
    "SESSION_EXPIRED": 410,
    "UNKNOWN": 500,
}
