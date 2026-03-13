# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Response models and error codes for OpenViking HTTP Server."""

from typing import Any, Optional

from pydantic import BaseModel


class ErrorInfo(BaseModel):
    """Error information."""

    code: str
    message: str
    details: Optional[dict] = None


class UsageInfo(BaseModel):
    """Usage information."""

    tokens: Optional[int] = None
    vectors_scanned: Optional[int] = None


class Response(BaseModel):
    """Standard API response."""

    status: str  # "ok" | "error"
    result: Optional[Any] = None
    error: Optional[ErrorInfo] = None
    time: float = 0.0
    usage: Optional[UsageInfo] = None


# Error code to HTTP status code mapping
ERROR_CODE_TO_HTTP_STATUS = {
    "OK": 200,
    "INVALID_ARGUMENT": 400,
    "INVALID_URI": 400,
    "NOT_FOUND": 404,
    "ALREADY_EXISTS": 409,
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
