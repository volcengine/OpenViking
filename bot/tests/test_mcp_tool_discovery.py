"""Tool discovery against a real MCP server that paginates tools/list."""

import sys
from contextlib import AsyncExitStack

from vikingbot.agent.tools.mcp import connect_mcp_servers
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.config.schema import MCPServerConfig

PAGINATED_SERVER = """
import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

server = Server("paginated")


@server.list_tools()
async def list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
    second = bool(request.params and request.params.cursor == "page-2")
    tool = types.Tool(name="second" if second else "first", inputSchema={"type": "object"})
    return types.ListToolsResult(tools=[tool], nextCursor=None if second else "page-2")


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


anyio.run(main)
"""


async def test_connect_registers_tools_from_every_page():
    registry = ToolRegistry()
    config = MCPServerConfig(command=sys.executable, args=["-c", PAGINATED_SERVER])

    async with AsyncExitStack() as stack:
        await connect_mcp_servers({"srv": config}, registry, stack)

    assert registry.has("mcp_srv_first")
    assert registry.has("mcp_srv_second")
