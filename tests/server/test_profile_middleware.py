# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx
import pytest
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, StreamingResponse

from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.profile_middleware import PROFILE_TOP_N, _sanitize_profile_path


def _make_test_app():
    class _Service:
        _initialized = True

        async def initialize(self):
            pass

        async def close(self):
            pass

    return create_app(config=ServerConfig(profile_enabled=True), service=_Service())


def _make_test_app_with_config(config: ServerConfig):
    class _Service:
        _initialized = True

        async def initialize(self):
            pass

        async def close(self):
            pass

    return create_app(config=config, service=_Service())


@pytest.mark.asyncio
async def test_profile_query_adds_profile_field_to_json_response():
    app = _make_test_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health", params={"profile": "1"})

    assert resp.status_code == 200
    body = resp.json()
    assert "profile" in body
    assert isinstance(body["profile"], list)
    assert any("ncalls" in line for line in body["profile"])
    assert any("cumtime" in line for line in body["profile"])
    assert not any(
        "/Users/bytedance/github_openviking/OpenViking/" in line for line in body["profile"]
    )
    assert not any("/site-packages/" in line for line in body["profile"])
    assert not any("/lib/python" in line for line in body["profile"])
    assert any("openviking/" in line or "tests/" in line for line in body["profile"])
    assert any(
        "starlette/" in line or "fastapi/" in line or "asyncio/" in line for line in body["profile"]
    )


@pytest.mark.asyncio
async def test_profile_query_does_not_affect_following_request():
    app = _make_test_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        profiled = await client.get("/health", params={"profile": "1"})
        plain = await client.get("/health")

    assert profiled.status_code == 200
    assert isinstance(profiled.json()["profile"], list)
    assert plain.status_code == 200
    assert "profile" not in plain.json()


@pytest.mark.asyncio
async def test_profile_query_is_ignored_when_server_profile_disabled():
    app = _make_test_app_with_config(ServerConfig(profile_enabled=False))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/health", params={"profile": "1"})

    assert resp.status_code == 200
    assert "profile" not in resp.json()


@pytest.mark.asyncio
async def test_profile_query_does_not_rewrite_plain_text_response():
    app = _make_test_app()
    router = APIRouter()

    @router.get("/plain")
    async def plain():
        return PlainTextResponse("ok")

    app.include_router(router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/plain", params={"profile": "1"})

    assert resp.status_code == 200
    assert resp.text == "ok"
    assert resp.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_profile_query_does_not_rewrite_streaming_response():
    app = _make_test_app()
    router = APIRouter()

    async def _iterator():
        yield b"streamed"

    @router.get("/stream")
    async def stream():
        return StreamingResponse(_iterator(), media_type="text/plain")

    app.include_router(router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/stream", params={"profile": "1"})

    assert resp.status_code == 200
    assert resp.text == "streamed"
    assert resp.headers["content-type"].startswith("text/plain")


def test_sanitize_profile_path_prefers_package_root_over_project_root_for_venv_packages():
    path = (
        "/Users/bytedance/github_openviking/OpenViking/"
        ".venv/lib/python3.11/site-packages/starlette/middleware/base.py"
    )

    assert _sanitize_profile_path(path) == "starlette/middleware/base.py"


def test_profile_default_top_n_is_100():
    assert PROFILE_TOP_N == 100
