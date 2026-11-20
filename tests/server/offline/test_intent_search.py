# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Offline session-aware search through the real service and retrieval stack."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.core.directories import DirectoryInitializer
from openviking.message import TextPart
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve import ContextType
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.retrieval_config import RetrievalConfig

SESSION_MESSAGE = "I am implementing OAuth authorization code authentication."
RAW_QUERY = "authentication"
EXPANDED_QUERY = "OAuth authorization code authentication documentation"


@pytest.fixture(autouse=True)
def skip_preset_directory_vectors(monkeypatch):
    # A preset skill would match the condition that the local index should skip.
    monkeypatch.setattr(DirectoryInitializer, "_ensure_directory_l0_l1_vectors", AsyncMock())


@pytest.fixture
async def indexed_resource(service):
    # Seed the local index directly so these tests isolate retrieval from resource import.
    root = "viking://resources/oauth"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    storage = service.viking_fs._get_vector_store()
    assert await storage.upsert(
        {
            "id": "oauth",
            "uri": f"{root}/oauth.md",
            "account_id": ctx.account_id,
            "context_type": "resource",
            "level": 2,
            "vector": [0.1] * service.viking_fs.query_embedder.get_dimension(),
        },
        ctx=ctx,
    )
    return root, ctx


@pytest.fixture
def intent_planner(service, monkeypatch):
    complete = AsyncMock()
    monkeypatch.setattr(
        service.viking_fs._vlm_resolver,
        "get_query_planner",
        AsyncMock(return_value=SimpleNamespace(get_completion_async=complete)),
    )

    embedded = []
    provider = service.viking_fs._embedding_provider
    original_embed = provider.embed

    async def record_embed(account_id, content, *, is_query=False):
        if is_query:
            embedded.append(content)
        return await original_embed(account_id, content, is_query=is_query)

    monkeypatch.setattr(provider, "embed", record_embed)
    return complete, embedded


@pytest.fixture
async def session_search(client, indexed_resource, intent_planner):
    root, _ = indexed_resource
    complete, embedded = intent_planner

    response = await client.post("/api/v1/sessions", json={})
    assert response.status_code == 200, response.text
    session_id = response.json()["result"]["session_id"]
    response = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": SESSION_MESSAGE},
    )
    assert response.status_code == 200, response.text

    async def search(**fields):
        response = await client.post(
            "/api/v1/search/search",
            json={
                "query": RAW_QUERY,
                "session_id": session_id,
                "level": [2],
                **fields,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["result"]

    return search, complete, embedded, root


def _assert_resource_hit(result, root):
    assert [(hit["uri"], hit["level"]) for hit in result["resources"]] == [(f"{root}/oauth.md", 2)]
    assert result["memories"] == result["skills"] == []


@pytest.mark.parametrize("scope_source", ["target_uri", "filter"])
async def test_search_recalls_resource_despite_conflicting_model_type(session_search, scope_source):
    search, complete, embedded, root = session_search
    complete.return_value = json.dumps(
        {"queries": [{"query": EXPANDED_QUERY, "context_type": "skill", "priority": 1}]}
    )
    fields = (
        {"target_uri": root}
        if scope_source == "target_uri"
        else {"filter": {"op": "must", "field": "context_type", "conds": ["resource"]}}
    )

    result = await search(**fields)

    _assert_resource_hit(result, root)
    assert "**Restricted Context Type**: resource" in complete.await_args.args[0]
    assert embedded == [EXPANDED_QUERY]
    [query] = result["query_plan"]["queries"]
    assert query["context_type"] == "resource"


@pytest.mark.parametrize(
    ("mixed_targets", "scope", "planned", "effective"),
    [
        (False, "resource", "memory", "resource"),
        (False, ["memory", "resource"], "skill", "resource"),
        (True, ["memory", "resource"], "skill", None),
        (True, ["memory", "resource"], "resource", "resource"),
        (True, None, "skill", None),
        (True, None, "resource", "resource"),
    ],
)
async def test_search_expands_session_context_and_keeps_caller_scope(
    session_search, mixed_targets, scope, planned, effective
):
    search, complete, embedded, root = session_search
    complete.return_value = json.dumps(
        {"queries": [{"query": EXPANDED_QUERY, "context_type": planned, "priority": 1}]}
    )

    target = [root, "viking://user/default/memories"] if mixed_targets else root
    result = await search(target_uri=target, context_type=scope)

    _assert_resource_hit(result, root)
    prompt = complete.await_args.args[0]
    assert f"[user]: {SESSION_MESSAGE}" in prompt
    if mixed_targets:
        assert "**Restricted Context Type**" not in prompt
    else:
        assert "**Restricted Context Type**: resource" in prompt
    assert embedded == [EXPANDED_QUERY]
    [query] = result["query_plan"]["queries"]
    assert query["query"] == EXPANDED_QUERY
    assert query["context_type"] == effective


async def test_search_executes_conflicting_caller_constraints(session_search):
    search, complete, embedded, root = session_search
    complete.return_value = json.dumps(
        {"queries": [{"query": EXPANDED_QUERY, "context_type": "skill", "priority": 1}]}
    )

    result = await search(
        target_uri=root, filter={"op": "must", "field": "context_type", "conds": ["memory"]}
    )

    assert result["total"] == 0
    assert "**Restricted Context Type**" not in complete.await_args.args[0]
    assert embedded == [EXPANDED_QUERY]
    [query] = result["query_plan"]["queries"]
    assert query["context_type"] is None


async def test_search_preserves_hits_with_skippable_type_filter(session_search):
    search, complete, embedded, root = session_search
    complete.return_value = json.dumps(
        {"queries": [{"query": EXPANDED_QUERY, "context_type": "resource", "priority": 1}]}
    )

    result = await search(
        filter={
            "op": "and",
            "ignore_empty_condition": True,
            "conds": [
                {"op": "must", "field": "context_type", "conds": ["skill"]},
                {"op": "must", "field": "level", "conds": [2]},
            ],
        }
    )

    _assert_resource_hit(result, root)
    assert "**Restricted Context Type**" not in complete.await_args.args[0]
    assert embedded == [EXPANDED_QUERY]


async def test_python_search_enforces_explicit_type_against_conflicting_target(
    indexed_resource, intent_planner, service
):
    root, ctx = indexed_resource
    complete, embedded = intent_planner
    complete.return_value = json.dumps(
        {"queries": [{"query": EXPANDED_QUERY, "context_type": "resource", "priority": 1}]}
    )
    session = service.sessions.session(ctx)
    await session.ensure_exists()
    await session.add_message_async("user", [TextPart(SESSION_MESSAGE)])

    result = await service.search.search(
        query=RAW_QUERY,
        ctx=ctx,
        target_uri=root,
        session=session,
        context_types=[ContextType.MEMORY],
        level=[2],
    )

    assert result.total == 0
    complete.assert_awaited_once()
    assert embedded == [EXPANDED_QUERY]


async def test_search_keeps_model_type_when_any_target_has_unknown_type(session_search):
    search, complete, _, root = session_search
    complete.return_value = json.dumps(
        {"queries": [{"query": EXPANDED_QUERY, "context_type": "skill", "priority": 1}]}
    )

    result = await search(target_uri=[root, "viking://user/default"])

    assert "**Restricted Context Type**" not in complete.await_args.args[0]
    [query] = result["query_plan"]["queries"]
    assert query["context_type"] == "skill"


async def test_search_returns_no_hits_for_valid_empty_plan(session_search):
    search, complete, embedded, root = session_search
    complete.return_value = json.dumps({"queries": []})

    result = await search(target_uri=root)

    assert result["total"] == 0
    assert result["query_plan"]["queries"] == []
    complete.assert_awaited_once()
    assert embedded == []


async def test_search_uses_raw_query_when_intent_is_disabled(session_search, service, monkeypatch):
    search, complete, embedded, root = session_search
    monkeypatch.setattr(service.viking_fs, "retrieval_config", RetrievalConfig(enable_intent=False))
    load_session = Mock()
    monkeypatch.setattr(service.sessions, "session", load_session)

    result = await search(target_uri=root)

    _assert_resource_hit(result, root)
    load_session.assert_not_called()
    complete.assert_not_awaited()
    assert embedded == [RAW_QUERY]
    assert "query_plan" not in result


@pytest.mark.parametrize("failure", ["timeout", "provider"])
async def test_search_uses_raw_query_when_intent_fails(
    session_search, service, monkeypatch, failure
):
    search, complete, embedded, root = session_search
    monkeypatch.setattr(
        service.viking_fs, "retrieval_config", RetrievalConfig(recall_intent_timeout_s=0.02)
    )

    async def fail(_prompt):
        if failure == "timeout":
            await asyncio.Event().wait()
        raise RuntimeError("provider overloaded")

    complete.side_effect = fail

    result = await asyncio.wait_for(search(target_uri=root), timeout=2)

    _assert_resource_hit(result, root)
    complete.assert_awaited_once()
    assert embedded == [RAW_QUERY]
    assert "query_plan" not in result
