# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP processing-time headers and request-header diagnostics."""

import logging
import time

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_access_logger = logging.getLogger("uvicorn.access")


class RequestTimingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter()
        if _access_logger.isEnabledFor(logging.DEBUG):
            request = Request(scope)
            _access_logger.debug(
                "Request headers for %s %s: %s",
                request.method,
                request.url.path,
                ", ".join(sorted(request.headers.keys())),
            )

        async def send_timed(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Process-Time"] = str(
                    time.perf_counter() - started_at
                )
            await send(message)

        await self.app(scope, receive, send_timed)
