# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import openviking.server.mcp_endpoint as endpoint
from openviking.core.item_search import SearchAsItemOptions
from openviking_cli.retrieve import FindResult


async def test_mcp_skill_find_forwards_explicit_filter(request_context, monkeypatch):
    find_skills = AsyncMock(return_value=FindResult(memories=[], resources=[], skills=[]))
    service = SimpleNamespace(search=SimpleNamespace(find_skills=find_skills))
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    metadata_filter = {"op": "must", "field": "item_attrs", "conds": ["project=P1"]}
    token = endpoint._mcp_ctx.set(request_context)
    try:
        await endpoint.find(query="tool", context_type="skill", filter=metadata_filter)
    finally:
        endpoint._mcp_ctx.reset(token)
    forwarded = find_skills.await_args.kwargs["filter"]
    assert metadata_filter in forwarded["conds"]
    assert {"op": "must", "field": "context_type", "conds": ["skill"]} in forwarded["conds"]


def test_mcp_find_and_search_keep_legacy_positional_arguments():
    find_arguments = list(inspect.signature(endpoint.find).parameters)
    assert find_arguments[:9] == [
        "query",
        "target_uri",
        "limit",
        "min_score",
        "level",
        "context_type",
        "read_content",
        "events_time_decay_protection",
        "search_as_item",
    ]
    search_arguments = list(inspect.signature(endpoint.search).parameters)
    assert search_arguments[8:11] == ["mode", "query_expansion", "max_tokens"]
    assert search_arguments[-1] == "filter"


async def test_mcp_search_keeps_empty_item_options_and_no_default_threshold(
    request_context, monkeypatch
):
    item = {
        "item_uuid": "uuid-a",
        "uri": "viking://resources/A",
        "context_type": "resource",
        "score": 1.79,
        "item_attrs": {},
        "item_extra_info": {
            "candidate_members_count": 1,
            "candidate_members_uri": ["viking://resources/A/a.md"],
        },
    }
    search = AsyncMock(return_value=FindResult.from_dict({"resources": [item]}))
    service = SimpleNamespace(
        search=SimpleNamespace(search=search, is_intent_enabled=lambda: False)
    )
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    token = endpoint._mcp_ctx.set(request_context)
    try:
        result = await endpoint.search(query="beach", search_as_item=SearchAsItemOptions())
    finally:
        endpoint._mcp_ctx.reset(token)
    assert "- [resource score 1.79] viking://resources/A" in result
    assert '"item_uuid": "uuid-a"' in result
    assert '"item_attrs": {}' in result
    assert '"items"' not in result
    assert search.await_args.kwargs["search_as_item"].aggregation == "max"
    assert search.await_args.kwargs["score_threshold"] is None


@pytest.mark.parametrize("item_mode", [False, True])
async def test_mcp_search_only_loads_a_session_for_ordinary_search(
    request_context, monkeypatch, item_mode
):
    session = SimpleNamespace(load=AsyncMock())
    factory = Mock(return_value=session)
    search = AsyncMock(return_value=FindResult(memories=[], resources=[], skills=[]))
    service = SimpleNamespace(
        sessions=SimpleNamespace(session=factory),
        search=SimpleNamespace(search=search, is_intent_enabled=lambda: True),
    )
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    token = endpoint._mcp_ctx.set(request_context)
    try:
        result = await endpoint.mcp.call_tool(
            "search",
            {
                "query": "find a Skill",
                "session_id": "unused",
                **({"search_as_item": {}} if item_mode else {}),
            },
        )
    finally:
        endpoint._mcp_ctx.reset(token)
    assert isinstance(result, tuple)
    search.assert_awaited_once()
    if item_mode:
        factory.assert_not_called()
        session.load.assert_not_awaited()
        assert search.await_args.kwargs["session"] is None
    else:
        factory.assert_called_once()
        session.load.assert_awaited_once()
        assert search.await_args.kwargs["session"] is session


async def test_mcp_find_forwards_item_filter_without_legacy_threshold(request_context, monkeypatch):
    item = {
        "item_uuid": "uuid-a",
        "uri": "viking://resources/A",
        "context_type": "resource",
        "score": 0.0,
        "item_attrs": {"asset_id": "V1"},
        "item_extra_info": {
            "candidate_members_count": 1,
            "candidate_members_uri": ["viking://resources/A/a.md"],
        },
    }
    find = AsyncMock(return_value=FindResult.from_dict({"resources": [item]}))
    service = SimpleNamespace(search=SimpleNamespace(find=find))
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    token = endpoint._mcp_ctx.set(request_context)
    try:
        result = await endpoint.find(
            query="",
            filter={"op": "must", "field": "item_attrs", "conds": ["asset_id=V1"]},
            search_as_item=SearchAsItemOptions(aggregation="sum"),
        )
    finally:
        endpoint._mcp_ctx.reset(token)
    assert "- [resource score 0] viking://resources/A" in result
    assert '"item_attrs": {"asset_id": "V1"}' in result
    assert '"candidate_members_count": 1' in result
    assert '"items"' not in result
    assert find.await_args.kwargs["filter"] == {
        "op": "must",
        "field": "item_attrs",
        "conds": ["asset_id=V1"],
    }
    assert find.await_args.kwargs["score_threshold"] is None
    assert find.await_args.kwargs["search_as_item"].aggregation == "sum"


async def test_mcp_item_search_rejects_time_decay(request_context, monkeypatch):
    search = AsyncMock()
    service = SimpleNamespace(
        search=SimpleNamespace(search=search, is_intent_enabled=lambda: False)
    )
    monkeypatch.setattr(endpoint, "get_service", lambda: service)
    token = endpoint._mcp_ctx.set(request_context)
    try:
        result = await endpoint.mcp.call_tool(
            "search",
            {
                "query": "beach",
                "search_as_item": {},
                "events_time_decay_protection": "1d",
            },
        )
    finally:
        endpoint._mcp_ctx.reset(token)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "INVALID_ARGUMENT"
    assert "time decay" in result.structuredContent["error"]["message"]
    search.assert_not_awaited()


async def test_mcp_item_skill_keeps_root_and_existing_output_without_summary_reads(
    request_context, monkeypatch
):
    root = "viking://user/owner/skills/S"
    result = FindResult.from_dict(
        {
            "skills": [
                {
                    "uri": root,
                    "context_type": "skill",
                    "score": 0.9,
                    "item_uuid": "uuid-skill",
                    "item_attrs": {},
                    "item_extra_info": {},
                }
            ]
        }
    )
    service = SimpleNamespace(fs=SimpleNamespace(abstract=AsyncMock()))
    rendered = await endpoint._format_search_result(result, service=service, ctx=request_context)
    assert f"- [skill score 0.9] {root}" in rendered
    assert "/SKILL.md" not in rendered
    assert '"item_uuid": "uuid-skill"' in rendered
    service.fs.abstract.assert_not_awaited()


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
