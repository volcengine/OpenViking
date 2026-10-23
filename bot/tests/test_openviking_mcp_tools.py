from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from vikingbot.agent.tools.base import MultimodalToolResult
from vikingbot.agent.tools.factory import register_default_tools
from vikingbot.agent.tools.ov_mcp import VikingFindMCPToolsTool, VikingInvokeMCPTool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.config.schema import BotMode, Config


@pytest.mark.asyncio
async def test_find_mcp_tools_returns_complete_discovery_payload(monkeypatch):
    result = {
        "tools": [
            {
                "uri": "viking://agent/tools/weather/current.json",
                "score": 0.91,
                "tool": {
                    "name": "current",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    "annotations": {"readOnlyHint": True},
                },
            }
        ],
        "total": 1,
    }
    client = SimpleNamespace(find_mcp_tools=AsyncMock(return_value=result))
    tool = VikingFindMCPToolsTool()
    monkeypatch.setattr(tool, "_get_client", AsyncMock(return_value=client))
    release = AsyncMock()
    monkeypatch.setattr(tool, "_release_client", release)

    output = await tool.execute(SimpleNamespace(), "current weather", limit=3)

    assert '"inputSchema"' in output
    assert '"readOnlyHint": true' in output
    client.find_mcp_tools.assert_awaited_once_with("current weather", limit=3)
    release.assert_awaited_once_with(ANY, client)


@pytest.mark.asyncio
async def test_invoke_mcp_tool_preserves_result_and_marks_upstream_error(monkeypatch):
    result = {
        "content": [{"type": "text", "text": "upstream rejected the request"}],
        "structuredContent": {"code": "invalid_city"},
        "isError": True,
    }
    client = SimpleNamespace(invoke_mcp_tool=AsyncMock(return_value=result))
    tool = VikingInvokeMCPTool()
    monkeypatch.setattr(tool, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(tool, "_release_client", AsyncMock())

    output = await tool.execute(
        SimpleNamespace(),
        "viking://agent/tools/weather/current.json",
        {"city": "Atlantis"},
    )

    assert output.startswith("Error:")
    assert '"structuredContent"' in output
    assert '"isError": true' in output
    client.invoke_mcp_tool.assert_awaited_once_with(
        "viking://agent/tools/weather/current.json",
        arguments={"city": "Atlantis"},
    )


@pytest.mark.asyncio
async def test_invoke_mcp_tool_exposes_image_content_to_model(monkeypatch):
    image_data = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    result = {
        "content": [
            {"type": "text", "text": "chart"},
            {"type": "image", "data": image_data, "mimeType": "image/png"},
        ],
        "structuredContent": {"kind": "chart"},
        "isError": False,
    }
    client = SimpleNamespace(invoke_mcp_tool=AsyncMock(return_value=result))
    tool = VikingInvokeMCPTool()
    monkeypatch.setattr(tool, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(tool, "_release_client", AsyncMock())

    output = await tool.execute(
        SimpleNamespace(),
        "viking://agent/tools/chart/render.json",
        {},
    )

    assert isinstance(output, MultimodalToolResult)
    assert '"structuredContent"' in output.text
    assert image_data not in output.text
    assert output.content[-1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{image_data}"},
    }


def test_openviking_mcp_tool_registration_respects_readonly_mode():
    normal = Config()
    normal_registry = ToolRegistry(normal)
    register_default_tools(normal_registry, normal)

    assert "openviking_find_mcp_tools" in normal_registry
    assert "openviking_invoke_mcp_tool" in normal_registry

    readonly = Config(mode=BotMode.READONLY)
    readonly_registry = ToolRegistry(readonly)
    register_default_tools(readonly_registry, readonly)

    assert "openviking_find_mcp_tools" in readonly_registry
    assert "openviking_invoke_mcp_tool" not in readonly_registry
