# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openviking.server.mcp_endpoint as endpoint
from openviking.core.item_search import ItemSearchResult, SearchAsItemOptions


async def test_mcp_search_keeps_empty_item_options_and_no_default_threshold(
    request_context, monkeypatch
):
    item = {
        "item_uuid": "uuid-a",
        "uri": "viking://resources/A",
        "item_score": 0.8,
        "item_attrs": {},
        "item_extra_info": {
            "candidate_members_count": 1,
            "candidate_members_uri": ["viking://resources/A/a.md"],
        },
    }
    search = AsyncMock(return_value=ItemSearchResult([item]))
    service = SimpleNamespace(
        search=SimpleNamespace(search=search, is_intent_enabled=lambda: False)
    )
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    token = endpoint._mcp_ctx.set(request_context)
    try:
        result = await endpoint.search(query="beach", search_as_item=SearchAsItemOptions())
    finally:
        endpoint._mcp_ctx.reset(token)
    assert json.loads(result) == {"items": [item], "total": 1}
    assert search.await_args.kwargs["search_as_item"].aggregation == "max"
    assert search.await_args.kwargs["score_threshold"] is None


async def test_mcp_add_resource_forwards_item_declaration(request_context, monkeypatch):
    add_resource = AsyncMock(return_value={"status": "accepted", "task_id": "task-1"})
    service = SimpleNamespace(resources=SimpleNamespace(add_resource=add_resource))
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    token = endpoint._mcp_ctx.set(request_context)
    try:
        result = await endpoint.add_resource(
            path="https://example.com/a.md",
            as_item=True,
            item_attrs={},
        )
    finally:
        endpoint._mcp_ctx.reset(token)
    assert "task-1" in result
    assert add_resource.await_args.kwargs["as_item"] is True
    assert add_resource.await_args.kwargs["item_attrs"] == {}


async def test_mcp_rejects_item_attrs_when_as_item_is_false(request_context, monkeypatch):
    add_resource = AsyncMock()
    service = SimpleNamespace(resources=SimpleNamespace(add_resource=add_resource))
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    token = endpoint._mcp_ctx.set(request_context)
    try:
        result = await endpoint.add_resource(
            path="https://example.com/a.md",
            as_item=False,
            item_attrs={},
        )
    finally:
        endpoint._mcp_ctx.reset(token)
    assert "item_attrs" in result
    add_resource.assert_not_awaited()
