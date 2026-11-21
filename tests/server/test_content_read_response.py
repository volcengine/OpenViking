# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public read-response contracts through actual FastAPI serialization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import content
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def read_app(monkeypatch):
    app = FastAPI()
    app.include_router(content.router)
    # The same endpoint without a response model exercises the previous encoder.
    app.add_api_route("/legacy/read", content.read, methods=["GET"], response_model=None)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    app.dependency_overrides[content.get_request_context] = lambda: ctx
    service = SimpleNamespace(
        fs=SimpleNamespace(read=AsyncMock(), read_visible=AsyncMock()),
        mcps=SimpleNamespace(read_live_context=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr(content, "get_service", lambda: service)
    return app, service, ctx


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize(
    "body", ["", "ascii\n" * 11000, "中文🙂\n", '"\\\r\n\t\x00', "\u2028\u2029"]
)
async def test_read_response_preserves_json_and_headers(read_app, raw, body):
    app, service, ctx = read_app
    service.fs.read.return_value = body
    service.fs.read_visible.return_value = body
    params = {"uri": "viking://resources/sample.txt", "raw": str(raw).lower()}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/content/read", params=params)
        legacy = await client.get("/legacy/read", params=params)
    assert response.status_code == legacy.status_code == 200
    assert (
        response.json()
        == legacy.json()
        == {
            "status": "ok",
            "result": body,
            "error": None,
            "telemetry": None,
            "profile": None,
        }
    )
    assert response.headers["content-type"] == "application/json"
    assert int(response.headers["content-length"]) == len(response.content)
    reader = service.fs.read if raw else service.fs.read_visible
    assert reader.await_args.kwargs == {"ctx": ctx, "offset": 0, "limit": -1}


@pytest.mark.parametrize("raw", [False, True])
async def test_read_response_preserves_live_context_selection(read_app, raw):
    app, service, _ = read_app
    service.fs.read.return_value = "stored"
    service.fs.read_visible.return_value = "visible"
    service.mcps.read_live_context.return_value = "live"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/content/read",
            params={"uri": "viking://resources/sample.txt", "raw": str(raw).lower()},
        )
    assert response.json()["result"] == ("stored" if raw else "live")
    assert service.mcps.read_live_context.await_count == (0 if raw else 1)


async def test_read_response_preserves_missing_uri_validation(read_app):
    app, service, _ = read_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/content/read")
    assert response.status_code == 422
    service.fs.read.assert_not_awaited()
    service.fs.read_visible.assert_not_awaited()


def test_read_openapi_describes_standard_response(read_app):
    app, _, _ = read_app
    schema = app.openapi()["paths"]["/api/v1/content/read"]["get"]["responses"]["200"]
    assert schema["content"]["application/json"]["schema"]["$ref"].endswith("/Response")
