# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Request ID handling for the OpenViking HTTP server."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openviking_cli.utils.logger import bind_log_request_id

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES = b"x-request-id"
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_IGNORED_COMPLETION_ROUTES = frozenset({"/health", "/ready", "/metrics"})

_completion_logger = logging.getLogger("openviking.observability.http")


def _resolve_request_id(scope: Scope) -> tuple[str, bool]:
    values = [
        value
        for name, value in scope["headers"]
        if name.lower() == _REQUEST_ID_HEADER_BYTES
    ]
    invalid = bool(values)
    if len(values) == 1:
        try:
            value = values[0].decode("ascii")
        except UnicodeDecodeError:
            pass
        else:
            if _REQUEST_ID_PATTERN.fullmatch(value):
                return value, False
    return str(uuid.uuid4()), invalid


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "/__unmatched__"


class RequestIdMiddleware:
    """Validate, bind, return, and log one request ID per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter()
        request_id, invalid = _resolve_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        method = scope["method"]
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != _REQUEST_ID_HEADER_BYTES
                ]
                headers.append((_REQUEST_ID_HEADER_BYTES, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with bind_log_request_id(request_id):
            try:
                if invalid:
                    response = JSONResponse(
                        status_code=400,
                        content={
                            "status": "error",
                            "error": {
                                "code": "INVALID_ARGUMENT",
                                "message": (
                                    "X-Request-ID must be supplied once and contain 1-128 "
                                    "characters from [A-Za-z0-9._:-]"
                                ),
                            },
                        },
                    )
                    await response(scope, receive, send_with_request_id)
                else:
                    await self.app(scope, receive, send_with_request_id)
            finally:
                if scope["path"] not in _IGNORED_COMPLETION_ROUTES:
                    duration_ms = (time.perf_counter() - started_at) * 1000
                    route = _route_template(scope)
                    _completion_logger.info(
                        "HTTP request completed method=%s route=%s status=%d duration_ms=%.1f",
                        method,
                        route,
                        status_code,
                        duration_ms,
                        extra={
                            "http_method": method,
                            "http_route": route,
                            "http_status_code": status_code,
                            "duration_ms": duration_ms,
                        },
                    )
