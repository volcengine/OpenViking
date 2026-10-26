from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.ov_file import VikingAddResourceTool
from vikingbot.openviking_mount.ov_server import VikingClient


@pytest.mark.asyncio
@pytest.mark.parametrize("target_uri", [None, "viking://resources/feishu/doc/config"])
async def test_add_resource_tool_submits_to_sdk(monkeypatch, target_uri):
    expected_uri = target_uri or "viking://resources/doc"
    sdk_client = AsyncMock()
    sdk_client.add_resource.return_value = {"root_uri": expected_uri}
    client = object.__new__(VikingClient)
    client.client = sdk_client
    tool = VikingAddResourceTool()
    monkeypatch.setattr(tool, "_get_client", AsyncMock(return_value=client))

    kwargs = {"path": "https://example.com/doc", "description": "conversation export"}
    if target_uri is not None:
        kwargs["to"] = target_uri
    result = await tool.execute(ToolContext(), **kwargs)

    sdk_client.add_resource.assert_awaited_once_with(
        path="https://example.com/doc",
        to=target_uri,
        options={"reason": "conversation export"},
    )
    assert result == f"Successfully added resource: {expected_uri}"
