# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Auth enforcement tests for bot proxy routes."""

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.routers.bot import router as bot_router
from openviking.server.routers.bot import set_bot_api_url


@pytest.fixture
def bot_test_app() -> FastAPI:
    """Create a lightweight app exposing only bot routes."""
    app = FastAPI()
    set_bot_api_url("http://backend.example")
    app.include_router(bot_router, prefix="/bot/v1")
    return app


@pytest.mark.asyncio
async def test_chat_requires_api_key(bot_test_app: FastAPI):
    """Unauthenticated chat requests should be rejected with 401."""
    transport = httpx.ASGITransport(app=bot_test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/bot/v1/chat", json={"message": "hello"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_stream_requires_api_key(bot_test_app: FastAPI):
    """Unauthenticated streaming chat requests should be rejected with 401."""
    transport = httpx.ASGITransport(app=bot_test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/bot/v1/chat/stream", json={"message": "hello"})
    assert resp.status_code == 401
