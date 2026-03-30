# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for bot proxy endpoint auth enforcement."""

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request

import openviking.server.routers.bot as bot_router_module


def make_request(headers: dict[str, str]) -> Request:
    """Create a minimal request object with the provided headers."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in headers.items()
            ],
            "query_string": b"",
        }
    )


@pytest_asyncio.fixture
async def bot_auth_client() -> httpx.AsyncClient:
    """Client mounted with bot router and bot backend configured."""
    app = FastAPI()
    old_bot_api_url = bot_router_module.BOT_API_URL
    bot_router_module.set_bot_api_url("http://bot-backend.local")
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        bot_router_module.BOT_API_URL = old_bot_api_url


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"X-API-Key": "test-key"}, "test-key"),
        ({"Authorization": "Bearer test-token"}, "test-token"),
    ],
)
def test_extract_auth_token(headers: dict[str, str], expected: str):
    """Accepted auth header formats should both produce a token."""
    assert bot_router_module.extract_auth_token(make_request(headers)) == expected


def test_require_auth_token_rejects_missing_token():
    """Missing credentials should raise a 401 before proxying."""
    with pytest.raises(HTTPException) as exc_info:
        bot_router_module.require_auth_token(make_request({}))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing authentication token"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/bot/v1/chat", "/bot/v1/chat/stream"])
async def test_bot_proxy_requires_auth_token(bot_auth_client: httpx.AsyncClient, path: str):
    """Bot proxy endpoints should reject missing auth with 401."""
    response = await bot_auth_client.post(
        path,
        json={"message": "hello"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing authentication token"
