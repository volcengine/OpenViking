# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""The MCP ``search`` tool must bound its context arguments the way ``POST /search`` does.

``SearchRequest`` constrains four of them with pydantic ``Field``; the tool declared them
as plain ints and a plain list, so the MCP face accepted values the REST face rejects.

``exclude_uris`` is the one where that is not merely permissive: ``normalize_exclude_uris``
slices to ``MAX_EXCLUDE_URIS`` regardless, so a caller passing more got the extra ones
dropped in silence and the URIs it asked to exclude came back in the results. Over REST the
same request is a validation error.

Check the tool's published input schema and the errors returned to an MCP client.
"""

from datetime import timedelta

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.retrieve.context_assembler import MAX_EXCLUDE_URIS
from openviking.server.routers.search import FindRequest, SearchRequest

# name -> (REST field bound as it appears in the JSON schema, out-of-range value)
BOUNDED = {
    "max_tokens": ({"minimum": 64, "maximum": 32000}, 32001),
    "dedup_turns": ({"minimum": 0, "maximum": 100}, 101),
    "rewrite_max_bullets": ({"minimum": 1, "maximum": 20}, 21),
}


async def _search_schema() -> dict:
    tools = await mcp_endpoint.mcp.list_tools()
    search = next(tool for tool in tools if tool.name == "search")
    return search.inputSchema["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,expected", sorted((n, b) for n, (b, _) in BOUNDED.items()))
async def test_schema_publishes_the_same_bounds_as_rest(name, expected):
    # The bounds have to reach the schema, not just the validator: it is what the model
    # driving the tool reads before it picks a value.
    prop = (await _search_schema())[name]

    rest = SearchRequest.model_json_schema()["properties"][name]
    assert {key: prop.get(key) for key in expected} == expected
    assert {key: rest.get(key) for key in expected} == expected


@pytest.mark.asyncio
async def test_exclude_uris_schema_carries_the_shared_cap():
    prop = (await _search_schema())["exclude_uris"]

    rest = SearchRequest.model_json_schema()["properties"]["exclude_uris"]
    assert prop.get("maxItems") == rest.get("maxItems") == MAX_EXCLUDE_URIS


@pytest.mark.asyncio
@pytest.mark.parametrize("name,value", sorted((n, v) for n, (_, v) in BOUNDED.items()))
async def test_call_tool_rejects_an_out_of_range_value(name, value):
    async with create_connected_server_and_client_session(
        mcp_endpoint.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        result = await client.call_tool("search", {"query": "q", "mode": "context", name: value})

    assert result.isError is True
    assert any(isinstance(block, TextContent) and name in block.text for block in result.content)
    with pytest.raises(ValidationError) as rest:
        SearchRequest(query="q", mode="context", **{name: value})
    assert [(error["loc"], error["type"]) for error in rest.value.errors()] == [
        ((name,), "less_than_equal")
    ]


@pytest.mark.asyncio
async def test_call_tool_rejects_more_exclusions_than_it_can_apply():
    too_many = [f"viking://user/u/memories/m{i}.md" for i in range(MAX_EXCLUDE_URIS + 1)]
    async with create_connected_server_and_client_session(
        mcp_endpoint.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        result = await client.call_tool(
            "search", {"query": "q", "mode": "context", "exclude_uris": too_many}
        )

    assert result.isError is True
    assert any(
        isinstance(block, TextContent) and "exclude_uris" in block.text for block in result.content
    )
    with pytest.raises(ValidationError) as rest:
        SearchRequest(query="q", mode="context", exclude_uris=too_many)
    assert [(error["loc"], error["type"]) for error in rest.value.errors()] == [
        (("exclude_uris",), "too_long")
    ]


async def test_rest_and_mcp_reject_invalid_event_time_decay_requests():
    with pytest.raises(ValidationError, match="events_time_decay_protection"):
        SearchRequest(query="q", events_time_decay_protection="-1d")
    result = await mcp_endpoint.mcp.call_tool(
        "search", {"query": "q", "events_time_decay_protection": "-1d"}
    )
    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
    assert "events_time_decay_protection" in result.structuredContent["error"]["message"]
    with pytest.raises(ValidationError, match="semantic query or image"):
        FindRequest(
            filter={"op": "must", "field": "level", "conds": [2]}, events_time_decay_protection="0"
        )
