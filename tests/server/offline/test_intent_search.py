# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Offline session-aware search through the real HTTP and retrieval stack."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking_cli.utils.config.retrieval_config import RetrievalConfig

SESSION_MESSAGE = "I am implementing OAuth authorization code authentication."
RAW_QUERY = "authentication"
EXPANDED_QUERY = "OAuth authorization code authentication documentation"


@pytest.fixture
async def session_search(offline_resource_api, service, monkeypatch):
    client, root = offline_resource_api
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
                "target_uri": root,
                "level": [2],
                **fields,
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert [(hit["uri"], hit["level"]) for hit in result["resources"]] == [
            (f"{root}/oauth.md", 2),
        ]
        assert result["memories"] == result["skills"] == []
        return result

    return search, complete, embedded


@pytest.mark.parametrize(
    ("scope", "planned", "effective"),
    [
        (None, "resource", "resource"),
        ("resource", "memory", "resource"),
        (["memory", "resource"], "skill", None),
        (["memory", "resource"], "resource", "resource"),
    ],
)
async def test_search_expands_session_context_and_keeps_caller_scope(
    session_search, scope, planned, effective
):
    search, complete, embedded = session_search
    complete.return_value = json.dumps(
        {"queries": [{"query": EXPANDED_QUERY, "context_type": planned, "priority": 1}]}
    )

    result = await search(context_type=scope)

    assert f"[user]: {SESSION_MESSAGE}" in complete.await_args.args[0]
    assert embedded == [EXPANDED_QUERY]
    [query] = result["query_plan"]["queries"]
    assert query["query"] == EXPANDED_QUERY
    assert query["context_type"] == effective


async def test_search_uses_raw_query_when_intent_is_disabled(session_search, service, monkeypatch):
    search, complete, embedded = session_search
    monkeypatch.setattr(service.viking_fs, "retrieval_config", RetrievalConfig(enable_intent=False))
    load_session = Mock()
    monkeypatch.setattr(service.sessions, "session", load_session)

    result = await search()

    load_session.assert_not_called()
    complete.assert_not_awaited()
    assert embedded == [RAW_QUERY]
    assert "query_plan" not in result


@pytest.mark.parametrize("failure", ["timeout", "provider"])
async def test_search_uses_raw_query_when_intent_fails(
    session_search, service, monkeypatch, failure
):
    search, complete, embedded = session_search
    monkeypatch.setattr(
        service.viking_fs, "retrieval_config", RetrievalConfig(recall_intent_timeout_s=0.02)
    )

    async def fail(_prompt):
        if failure == "timeout":
            await asyncio.Event().wait()
        raise RuntimeError("provider overloaded")

    complete.side_effect = fail

    result = await asyncio.wait_for(search(), timeout=2)

    complete.assert_awaited_once()
    assert embedded == [RAW_QUERY]
    assert "query_plan" not in result
