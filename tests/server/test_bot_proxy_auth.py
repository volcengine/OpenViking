# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for bot proxy endpoint auth enforcement."""

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import openviking.server.routers.bot as bot_router_module


@pytest_asyncio.fixture
async def bot_auth_client() -> httpx.AsyncClient:
    """Client mounted with bot router and bot backend configured."""
    app = FastAPI()
    bot_router_module.set_bot_api_url("http://bot-backend.local")
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_chat_requires_auth_token(bot_auth_client: httpx.AsyncClient):
    """POST /bot/v1/chat should reject missing auth with 401."""
    response = await bot_auth_client.post(
        "/bot/v1/chat",
        json={"message": "hello"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing authentication token"


@pytest.mark.asyncio
async def test_chat_stream_requires_auth_token(bot_auth_client: httpx.AsyncClient):
    """POST /bot/v1/chat/stream should reject missing auth with 401."""
    response = await bot_auth_client.post(
        "/bot/v1/chat/stream",
        json={"message": "hello"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing authentication token"
