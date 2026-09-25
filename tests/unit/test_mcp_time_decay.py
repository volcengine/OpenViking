# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.retrieve.context_assembler import AssembleResult
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


async def test_find_exposes_and_forwards_event_time_decay(monkeypatch):
    captured = {}

    async def fake_find(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(memories=[], resources=[], skills=[])

    service = SimpleNamespace(search=SimpleNamespace(find=fake_find))
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    token = mcp_endpoint._mcp_ctx.set(
        RequestContext(
            user=UserIdentifier.the_default_user("test_user"),
            role=Role.ROOT,
        )
    )
    try:
        result = await mcp_endpoint.find(
            query="recent decision",
            context_type="memory",
            events_time_decay_protection="7d",
        )
        tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    finally:
        mcp_endpoint._mcp_ctx.reset(token)

    assert result == "No matching context found."
    assert captured["events_time_decay_protection"] == "7d"
    assert "events_time_decay_protection" in tools["find"].inputSchema["properties"]


async def test_skill_only_find_rejects_invalid_event_time_decay(monkeypatch):
    async def fake_find_skills(**kwargs):
        raise AssertionError(f"invalid protection reached skill retrieval: {kwargs}")

    service = SimpleNamespace(search=SimpleNamespace(find_skills=fake_find_skills))
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    token = mcp_endpoint._mcp_ctx.set(
        RequestContext(
            user=UserIdentifier.the_default_user("test_user"),
            role=Role.ROOT,
        )
    )
    try:
        with pytest.raises(InvalidArgumentError, match="events_time_decay_protection"):
            await mcp_endpoint.find(
                query="skill",
                context_type="skill",
                events_time_decay_protection="bogus",
            )
    finally:
        mcp_endpoint._mcp_ctx.reset(token)


async def test_skill_only_find_preserves_negative_threshold_with_decay(monkeypatch):
    captured = {}

    async def fake_find_skills(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(memories=[], resources=[], skills=[])

    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(search=SimpleNamespace(find_skills=fake_find_skills)),
    )

    token = mcp_endpoint._mcp_ctx.set(
        RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)
    )
    try:
        await mcp_endpoint.find(
            query="skill",
            context_type="skill",
            min_score=-0.1,
            events_time_decay_protection="0",
        )
    finally:
        mcp_endpoint._mcp_ctx.reset(token)

    assert captured["score_threshold"] == -0.1


@pytest.mark.parametrize("mode", ["list", "context"])
async def test_search_rejects_invalid_event_time_decay_before_dispatch(monkeypatch, mode):
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: (_ for _ in ()).throw(AssertionError("invalid request was dispatched")),
    )

    with pytest.raises(InvalidArgumentError, match="events_time_decay_protection"):
        await mcp_endpoint.search(
            query="recent decision",
            mode=mode,
            events_time_decay_protection="bogus",
        )


async def test_search_context_forwards_event_time_decay(monkeypatch):
    captured = {}

    async def fake_assemble_context(*, service, ctx, params):
        del service, ctx
        captured["params"] = params
        return AssembleResult(rendered="<memory />")

    monkeypatch.setattr(mcp_endpoint, "assemble_context", fake_assemble_context)
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: SimpleNamespace())
    token = mcp_endpoint._mcp_ctx.set(
        RequestContext(
            user=UserIdentifier.the_default_user("test_user"),
            role=Role.ROOT,
        )
    )
    try:
        result = await mcp_endpoint.search(
            query="recent decision",
            mode="context",
            events_time_decay_protection="2d",
        )
    finally:
        mcp_endpoint._mcp_ctx.reset(token)

    assert result == "<memory />"
    assert captured["params"].events_time_decay_protection == "2d"


async def test_search_context_preserves_negative_threshold(monkeypatch):
    captured = {}

    async def fake_assemble_context(*, service, ctx, params):
        captured["threshold"] = params.score_threshold
        return AssembleResult()

    monkeypatch.setattr(mcp_endpoint, "assemble_context", fake_assemble_context)
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: SimpleNamespace())

    token = mcp_endpoint._mcp_ctx.set(
        RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)
    )
    try:
        await mcp_endpoint.search(
            query="skill",
            mode="context",
            min_score=-0.1,
            quotas={"skills": 1},
            events_time_decay_protection="0",
        )
    finally:
        mcp_endpoint._mcp_ctx.reset(token)

    assert captured["threshold"] == -0.1
