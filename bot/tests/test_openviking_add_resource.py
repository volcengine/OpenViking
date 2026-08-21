from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.ov_file import VikingAddResourceTool
from vikingbot.openviking_mount.ov_server import VikingClient


class _FakeClient:
    def __init__(self, root_uri):
        self.root_uri = root_uri
        self.calls = []

    async def add_resource(self, path, description, to=None):
        self.calls.append((path, description, to))
        return {"root_uri": self.root_uri}


def test_add_resource_schema_exposes_optional_target_uri():
    tool = VikingAddResourceTool()

    assert tool.parameters["properties"]["to"] == {
        "type": "string",
        "description": "Optional exact target URI under viking://resources/. When omitted, OpenViking chooses the resource URI.",
    }
    assert "to" not in tool.parameters["required"]
    assert tool.validate_params({"path": "https://example.com/doc", "description": "doc"}) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_uri", "root_uri"),
    [
        (None, "viking://resources/doc"),
        ("viking://resources/feishu/doc/config", "viking://resources/feishu/doc/config"),
    ],
)
async def test_add_resource_tool_forwards_optional_target(monkeypatch, target_uri, root_uri):
    tool = VikingAddResourceTool()
    client = _FakeClient(root_uri)

    async def _fake_get_client(_tool_context):
        return client

    monkeypatch.setattr(tool, "_get_client", _fake_get_client)

    kwargs = {"path": "https://example.com/doc", "description": "conversation export"}
    if target_uri is not None:
        kwargs["to"] = target_uri
    result = await tool.execute(ToolContext(), **kwargs)

    assert client.calls == [("https://example.com/doc", "conversation export", target_uri)]
    assert result == f"Successfully added resource: {root_uri}"


@pytest.mark.asyncio
@pytest.mark.parametrize("target_uri", [None, "viking://resources/feishu/doc/config"])
async def test_viking_client_add_resource_forwards_optional_target(target_uri):
    expected_uri = target_uri or "viking://resources/doc"
    sdk_client = AsyncMock()
    sdk_client.add_resource.return_value = {"root_uri": expected_uri}
    client = object.__new__(VikingClient)
    client.client = sdk_client

    result = await client.add_resource("/tmp/doc.md", "conversation export", to=target_uri)

    sdk_client.add_resource.assert_awaited_once_with(
        path="/tmp/doc.md",
        to=target_uri,
        reason="conversation export",
    )
    assert result["root_uri"] == expected_uri
