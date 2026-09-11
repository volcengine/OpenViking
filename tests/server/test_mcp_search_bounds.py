# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""The MCP ``search`` tool must bound its context arguments the way ``POST /search`` does.

``SearchRequest`` constrains four of them with pydantic ``Field``; the tool declared them
as plain ints and a plain list, so the MCP face accepted values the REST face rejects.

``exclude_uris`` is the one where that is not merely permissive: ``normalize_exclude_uris``
slices to ``MAX_EXCLUDE_URIS`` regardless, so a caller passing more got the extra ones
dropped in silence and the URIs it asked to exclude came back in the results. Over REST the
same request is a validation error.

Bounds are asserted on the tool's published input schema and through ``call_tool``, which
is the path an MCP client actually takes — a direct call to the undecorated function is not
validated by either face.
"""

import pytest
from pydantic import ValidationError

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.retrieve.context_assembler import MAX_EXCLUDE_URIS
from openviking.server.routers.search import SearchRequest

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

    assert {key: prop.get(key) for key in expected} == expected


@pytest.mark.asyncio
async def test_exclude_uris_schema_carries_the_shared_cap():
    prop = (await _search_schema())["exclude_uris"]

    assert prop.get("maxItems") == MAX_EXCLUDE_URIS


@pytest.mark.asyncio
@pytest.mark.parametrize("name,value", sorted((n, v) for n, (_, v) in BOUNDED.items()))
async def test_call_tool_rejects_an_out_of_range_value(name, value):
    with pytest.raises(Exception) as excinfo:
        await mcp_endpoint.mcp.call_tool("search", {"query": "q", "mode": "context", name: value})

    assert name in str(excinfo.value)


@pytest.mark.asyncio
async def test_call_tool_rejects_more_exclusions_than_it_can_apply():
    too_many = [f"viking://user/u/memories/m{i}.md" for i in range(MAX_EXCLUDE_URIS + 1)]

    with pytest.raises(Exception) as excinfo:
        await mcp_endpoint.mcp.call_tool(
            "search", {"query": "q", "mode": "context", "exclude_uris": too_many}
        )

    assert "exclude_uris" in str(excinfo.value)


def test_rest_rejects_the_same_values():
    # The point of this file is parity, so pin the side being matched too: if a bound is
    # relaxed over REST, this fails next to the tool test that would then be wrong.
    for name, (_, value) in BOUNDED.items():
        with pytest.raises(ValidationError):
            SearchRequest(query="q", mode="context", **{name: value})

    with pytest.raises(ValidationError):
        SearchRequest(
            query="q",
            mode="context",
            exclude_uris=[f"viking://user/u/m{i}.md" for i in range(MAX_EXCLUDE_URIS + 1)],
        )
