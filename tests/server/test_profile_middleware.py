# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import sys

import httpx
import pytest
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.background import BackgroundTask

from openviking.metrics.datasources import HttpRequestLifecycleDataSource
from openviking.observability.context import get_root_observability_context
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
    completed = []

    @app.get("/profile-json")
    async def json_response():
        response = JSONResponse(
            {"status": "ok"}, background=BackgroundTask(completed.append, "done")
        )
        response.set_cookie("first", "1")
        response.set_cookie("second", "2")
        return response

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/profile-json", params={"profile": "1"})

    assert resp.status_code == 200
    body = resp.json()
    assert "profile" in body
    assert isinstance(body["profile"], list)
    assert int(resp.headers["content-length"]) == len(resp.content)
    assert len(resp.headers.get_list("set-cookie")) == 2
    assert completed == ["done"]
    assert float(resp.headers["x-process-time"]) >= 0
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
async def test_profile_query_does_not_rewrite_plain_text_or_file_response(tmp_path):
    app = _make_test_app()
    router = APIRouter()

    @router.get("/plain")
    async def plain():
        return PlainTextResponse("ok")

    path = tmp_path / "download.json"
    path.write_bytes(b'{"download":true}')

    @router.get("/file")
    async def file():
        return FileResponse(path)

    app.include_router(router)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/plain", params={"profile": "1"})
        file_resp = await client.get("/file", params={"profile": "1"})

    assert resp.status_code == 200
    assert resp.text == "ok"
    assert resp.headers["content-type"].startswith("text/plain")
    assert file_resp.content == path.read_bytes()
    assert int(file_resp.headers["content-length"]) == len(file_resp.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["complete", "disconnect", "cancel", "error"])
async def test_profile_query_preserves_stream_delivery_and_context_cleanup(ending, monkeypatch):
    app = _make_test_app()
    router = APIRouter()
    first_chunk = asyncio.Event()
    release = asyncio.Event()
    disconnect = asyncio.Event()
    generator_closed = asyncio.Event()
    messages = []
    completed_requests = []
    background = []
    monkeypatch.setattr(
        HttpRequestLifecycleDataSource,
        "record_request",
        lambda **kwargs: completed_requests.append(kwargs),
    )

    async def _iterator():
        try:
            assert get_root_observability_context().request_id == "stream-request"
            yield b'{"first":true}\n'
            await release.wait()
            assert get_root_observability_context().request_id == "stream-request"
            if ending == "error":
                raise RuntimeError("stream failed after headers")
            yield b'{"last":true}\n'
        finally:
            generator_closed.set()

    @router.get("/stream")
    async def stream():
        return StreamingResponse(
            _iterator(),
            media_type="application/json",
            background=BackgroundTask(background.append, "done"),
        )

    app.include_router(router)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/stream",
        "query_string": b"profile=1",
        "root_path": "",
        "headers": [(b"x-request-id", b"stream-request")],
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
    }

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk.set()

    async def serve():
        try:
            await app(scope, receive, send)
        finally:
            assert get_root_observability_context() is None
            assert sys.getprofile() is None

    task = asyncio.create_task(serve())
    try:
        await asyncio.wait_for(first_chunk.wait(), timeout=2)
        assert not task.done()
        assert len(completed_requests) == 1
        assert completed_requests[0]["status"] == "200"
        headers = dict(messages[0]["headers"])
        assert float(headers[b"x-process-time"]) >= 0
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif ending == "disconnect":
            disconnect.set()
            await asyncio.wait_for(task, timeout=2)
        else:
            release.set()
            if ending == "error":
                with pytest.raises(RuntimeError, match="response already started"):
                    await asyncio.wait_for(task, timeout=2)
            else:
                await asyncio.wait_for(task, timeout=2)
                assert background == ["done"]
        assert generator_closed.is_set()
        assert len(completed_requests) == 1
        body = b"".join(m.get("body", b"") for m in messages)
        assert body == (
            b'{"first":true}\n{"last":true}\n' if ending == "complete" else b'{"first":true}\n'
        )
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_sanitize_profile_path_prefers_package_root_over_project_root_for_venv_packages():
    path = (
        "/Users/bytedance/github_openviking/OpenViking/"
        ".venv/lib/python3.11/site-packages/starlette/middleware/base.py"
    )

    assert _sanitize_profile_path(path) == "starlette/middleware/base.py"


def test_profile_default_top_n_is_100():
    assert PROFILE_TOP_N == 100
