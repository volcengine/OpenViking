# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Real transport tests for VikingBot's MCP v2 client integration."""

import socket
import sys
import threading
import time
from contextlib import AsyncExitStack, contextmanager
from types import SimpleNamespace

import pytest
import uvicorn
from mcp.server import MCPServer
from starlette.responses import RedirectResponse
from vikingbot.agent.tools.mcp import connect_mcp_servers


class _RecordingRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


@contextmanager
def _serve(app):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        if not thread.is_alive():
            raise RuntimeError("MCP test server stopped during startup")
        time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("MCP test server did not start")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("MCP test server did not stop")


def _server_config(*, transport_type, url="", command="", args=None, headers=None):
    return SimpleNamespace(
        type=transport_type,
        command=command,
        args=args or [],
        env={},
        url=url,
        headers=headers or {},
        tool_timeout=5,
        enabled_tools=["*"],
    )


def _http_app(server, transport_type):
    if transport_type == "sse":
        return server.sse_app(), "/sse"
    return server.streamable_http_app(stateless_http=True), "/mcp"


def _redirect_app(target_url, fallback_app=None, recorded_api_keys=None):
    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/redirect":
            if recorded_api_keys is not None:
                headers = dict(scope["headers"])
                recorded_api_keys.append(headers.get(b"x-api-key"))
            response = RedirectResponse(target_url, status_code=307)
            await response(scope, receive, send)
            return
        if fallback_app is None:
            raise AssertionError(f"unexpected request path: {scope.get('path')}")
        await fallback_app(scope, receive, send)

    return app


def _record_requests(app, recorded_api_keys):
    async def wrapper(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            recorded_api_keys.append(headers.get(b"x-api-key"))
        await app(scope, receive, send)

    return wrapper


@pytest.mark.asyncio
async def test_vikingbot_connects_to_real_stdio_server():
    server_script = """
from mcp.server import MCPServer

server = MCPServer("stdio-test")

@server.tool()
def echo(value: str) -> str:
    return f"echo:{value}"

server.run(transport="stdio")
"""
    registry = _RecordingRegistry()
    config = _server_config(
        transport_type="stdio",
        command=sys.executable,
        args=["-c", server_script],
    )

    async with AsyncExitStack() as stack:
        await connect_mcp_servers({"stdio": config}, registry, stack)
        tool = registry.tools["mcp_stdio_echo"]
        result = await tool.execute(SimpleNamespace(), value="ready")

    assert result == "echo:ready"


@pytest.mark.parametrize("transport_type", ["sse", "streamableHttp"])
@pytest.mark.asyncio
async def test_vikingbot_connects_to_real_http_server(transport_type):
    server = MCPServer(f"{transport_type}-test")

    @server.tool()
    def echo(value: str) -> str:
        return f"echo:{value}"

    app = (
        server.sse_app()
        if transport_type == "sse"
        else server.streamable_http_app(stateless_http=True)
    )
    registry = _RecordingRegistry()

    with _serve(app) as base_url:
        suffix = "/sse" if transport_type == "sse" else "/mcp"
        config = _server_config(transport_type=transport_type, url=f"{base_url}{suffix}")
        async with AsyncExitStack() as stack:
            await connect_mcp_servers({"http": config}, registry, stack)
            tool = registry.tools["mcp_http_echo"]
            result = await tool.execute(SimpleNamespace(), value="ready")

    assert result == "echo:ready"


@pytest.mark.parametrize("transport_type", ["sse", "streamableHttp"])
@pytest.mark.asyncio
async def test_vikingbot_follows_same_origin_http_redirect(transport_type):
    server = MCPServer(f"{transport_type}-same-origin-redirect-test")

    @server.tool()
    def echo(value: str) -> str:
        return f"echo:{value}"

    mcp_app, target_path = _http_app(server, transport_type)
    registry = _RecordingRegistry()

    with _serve(_redirect_app(target_path, fallback_app=mcp_app)) as base_url:
        config = _server_config(
            transport_type=transport_type,
            url=f"{base_url}/redirect",
        )
        async with AsyncExitStack() as stack:
            await connect_mcp_servers({"redirected": config}, registry, stack)
            tool = registry.tools["mcp_redirected_echo"]
            result = await tool.execute(SimpleNamespace(), value="ready")

    assert result == "echo:ready"


@pytest.mark.parametrize("transport_type", ["sse", "streamableHttp"])
@pytest.mark.asyncio
async def test_vikingbot_rejects_cross_origin_http_redirect_without_leaking_api_key(
    transport_type,
):
    server = MCPServer(f"{transport_type}-cross-origin-redirect-test")

    @server.tool()
    def echo(value: str) -> str:
        return f"echo:{value}"

    mcp_app, target_path = _http_app(server, transport_type)
    first_origin_api_keys = []
    second_origin_api_keys = []
    registry = _RecordingRegistry()

    with _serve(_record_requests(mcp_app, second_origin_api_keys)) as second_origin:
        redirect_app = _redirect_app(
            f"{second_origin}{target_path}",
            recorded_api_keys=first_origin_api_keys,
        )
        with _serve(redirect_app) as first_origin:
            config = _server_config(
                transport_type=transport_type,
                url=f"{first_origin}/redirect",
                headers={"X-API-Key": "redirect-secret"},
            )
            async with AsyncExitStack() as stack:
                await connect_mcp_servers({"redirected": config}, registry, stack)

    assert first_origin_api_keys == [b"redirect-secret"]
    assert second_origin_api_keys == []
    assert registry.tools == {}
