# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Helpers for building consistent HTTP API response envelopes."""

from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse

from openviking.observability.http_error_context import capture_public_http_error
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.storage.vectordb.utils.json_safety import sanitize_unicode_for_json


class SurrogateSafeJSONResponse(JSONResponse):
    """``JSONResponse`` that tolerates lone surrogate code points in the payload.

    Filesystem names and tool results can carry isolated UTF-16 surrogates
    (U+D800-U+DFFF) after Python decodes non-UTF-8 bytes with ``surrogateescape``.
    Starlette's ``render`` calls ``json.dumps(..., ensure_ascii=False)`` and then
    encodes to UTF-8, which raises ``UnicodeEncodeError`` for those characters.
    On that failure we re-render the sanitized payload (lone surrogates become
    U+FFFD), so the response envelope stays well-formed instead of truncating
    into a 500. Normal responses are byte-for-byte identical and pay no cost.
    """

    def render(self, content: Any) -> bytes:
        try:
            return super().render(content)
        except UnicodeEncodeError:
            return super().render(sanitize_unicode_for_json(content))


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
        capture_public_http_error(
            code=error.code,
            message=error.message,
            details=error.details,
        )
        content = Response(
            status="error",
            error=error,
            telemetry=telemetry,
        ).model_dump(exclude_none=True)
        return SurrogateSafeJSONResponse(
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
    capture_public_http_error(code=code, message=message, details=details)
    content = Response(
        status="error",
        error=ErrorInfo(code=code, message=message, details=details),
        telemetry=telemetry,
    ).model_dump(exclude_none=True)
    return SurrogateSafeJSONResponse(
        status_code=ERROR_CODE_TO_HTTP_STATUS.get(code, 500),
        content=content,
    )
