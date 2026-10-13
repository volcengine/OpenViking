# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Keep a request stat cache alive through the complete ASGI response."""

from openviking.pyagfs.request_cache import RequestCacheScope, request_cache_scope


class RequestCacheMiddleware:
    def __init__(self, app, enabled):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled():
            return await self.app(scope, receive, send)
        cache = RequestCacheScope()
        token = request_cache_scope.set(cache)

        async def send_response(message):
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                # Starlette runs response background tasks after the final body.
                await cache.close()

        try:
            await self.app(scope, receive, send_response)
        finally:
            try:
                await cache.close()
            finally:
                request_cache_scope.reset(token)
