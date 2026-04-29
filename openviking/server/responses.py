# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for building consistent HTTP API response envelopes."""

from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse

from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response


def _message_from_business_error(result: Dict[str, Any]) -> str:
    message = result.get("message")
    if isinstance(message, str) and message:
        return message

    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, str) and first:
            return first
        if isinstance(first, dict):
            first_message = first.get("message")
            if isinstance(first_message, str) and first_message:
                return first_message
        return str(first)

    return "Operation failed"


def response_from_result(
    result: Any,
    *,
    telemetry: Optional[Dict[str, Any]] = None,
):
    """Build a standard API response from a synchronous operation result.

    Some service-layer operations historically returned ``{"status": "error"}``
    instead of raising an ``OpenVikingError``. At the HTTP boundary those are
    request failures, not successful results with an inner error payload.
    """
    if isinstance(result, dict) and result.get("status") == "error":
        code = result.get("code") or "PROCESSING_ERROR"
        if not isinstance(code, str) or not code:
            code = "PROCESSING_ERROR"

        details = result.get("details")
        error = ErrorInfo(
            code=code,
            message=_message_from_business_error(result),
            details=details if isinstance(details, dict) else None,
        )
        content = Response(
            status="error",
            error=error,
            telemetry=telemetry,
        ).model_dump(exclude_none=True)
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS.get(code, 500),
            content=content,
        )

    return Response(
        status="ok",
        result=result,
        telemetry=telemetry,
    ).model_dump(exclude_none=True)


def error_response(
    code: str,
    message: str,
    *,
    details: Optional[Dict[str, Any]] = None,
    telemetry: Optional[Dict[str, Any]] = None,
):
    """Build a standard API error response with the mapped HTTP status."""
    content = Response(
        status="error",
        error=ErrorInfo(code=code, message=message, details=details),
        telemetry=telemetry,
    ).model_dump(exclude_none=True)
    return JSONResponse(
        status_code=ERROR_CODE_TO_HTTP_STATUS.get(code, 500),
        content=content,
    )
