from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest
from mcp.types import Tool as MCPTool
from vikingbot.agent.tools.mcp import MCPToolWrapper, connect_mcp_servers


def test_mcp_tool_wrapper_uses_discovered_input_schema():
    tool = MCPTool(
        name="search",
        description="Search context",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    wrapper = MCPToolWrapper(None, "openviking", tool)

    assert wrapper.name == "mcp_openviking_search"
    assert wrapper.description == "Search context"
    assert wrapper.parameters == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }


@pytest.mark.parametrize("transport_type", ["sse", "streamableHttp"])
@pytest.mark.asyncio
async def test_connect_mcp_servers_uses_mcp2_http_transport(monkeypatch, transport_type):
    import mcp
    import mcp.client.sse
    import mcp.client.streamable_http

    observed = {}

    @asynccontextmanager
    async def fake_sse_client(url, *, httpx_client_factory):
        client = httpx_client_factory(
            headers={"X-MCP-SDK": "sdk-value"},
            timeout=httpx2.Timeout(None),
            auth=None,
        )
        observed["client_is_httpx2"] = isinstance(client, httpx2.AsyncClient)
        observed["follow_redirects"] = client.follow_redirects
        observed["configured_header"] = client.headers.get("X-Configured")
        observed["sdk_header"] = client.headers.get("X-MCP-SDK")
        async with client:
            yield object(), object()

    @asynccontextmanager
    async def fake_streamable_http_client(url, *, http_client):
        observed["client_is_httpx2"] = isinstance(http_client, httpx2.AsyncClient)
        observed["follow_redirects"] = http_client.follow_redirects
        observed["configured_header"] = http_client.headers.get("X-Configured")
        yield object(), object()

    class FakeClientSession:
        def __init__(self, read, write):
            self.read = read
            self.write = write

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def initialize(self):
            return None

        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    MCPTool(
                        name="search",
                        description="Search context",
                        inputSchema={"type": "object", "properties": {}},
                    )
                ]
            )

    class RecordingRegistry:
        def __init__(self):
            self.names = []

        def register(self, tool):
            self.names.append(tool.name)

    monkeypatch.setattr(mcp, "ClientSession", FakeClientSession)
    monkeypatch.setattr(mcp.client.sse, "sse_client", fake_sse_client)
    monkeypatch.setattr(
        mcp.client.streamable_http,
        "streamable_http_client",
        fake_streamable_http_client,
    )

    config = SimpleNamespace(
        type=transport_type,
        command="",
        args=[],
        env={},
        url="https://mcp.example.test/mcp",
        headers={"X-Configured": "config-value"},
        tool_timeout=30,
        enabled_tools=["*"],
    )
    registry = RecordingRegistry()

    async with AsyncExitStack() as stack:
        await connect_mcp_servers({"openviking": config}, registry, stack)

    assert observed["client_is_httpx2"] is True
    assert observed["follow_redirects"] is True
    assert observed["configured_header"] == "config-value"
    if transport_type == "sse":
        assert observed["sdk_header"] == "sdk-value"
    assert registry.names == ["mcp_openviking_search"]
