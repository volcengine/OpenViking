from types import SimpleNamespace

import pytest
from vikingbot.agent.tools.mcp import MCPToolWrapper


@pytest.mark.parametrize(
    "declared_types",
    (["object", "null"], ["null", "object"]),
)
def test_nullable_mcp_object_accepts_null_for_either_type_order(declared_types):
    tool = MCPToolWrapper(
        session=None,
        server_name="filters",
        tool_def=SimpleNamespace(
            name="search",
            description="Search with an optional object filter",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": declared_types,
                        "properties": {"field": {"type": "string"}},
                        "required": ["field"],
                    }
                },
                "required": ["filter"],
            },
        ),
    )

    assert tool.parameters["properties"]["filter"] == {
        "type": "object",
        "nullable": True,
        "properties": {"field": {"type": "string"}},
        "required": ["field"],
    }
    assert tool.validate_params({"filter": None}) == []
    assert tool.validate_params({"filter": {"field": "status"}}) == []
    assert tool.validate_params({"filter": "status"}) == ["filter should be object"]


def test_non_nullable_mcp_object_still_rejects_null():
    tool = MCPToolWrapper(
        session=None,
        server_name="filters",
        tool_def=SimpleNamespace(
            name="search",
            description="Search with a required object filter",
            inputSchema={
                "type": "object",
                "properties": {"filter": {"type": "object", "properties": {}}},
                "required": ["filter"],
            },
        ),
    )

    assert tool.validate_params({"filter": None}) == ["filter should be object"]
