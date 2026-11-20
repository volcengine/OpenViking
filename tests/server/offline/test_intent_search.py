# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Offline session-aware search through the real HTTP and retrieval stack.

Run: pytest tests/server/offline/
Only model calls are replaced; query planning and vector retrieval run normally.
"""

import json
from unittest.mock import AsyncMock, Mock


async def test_search_expands_session_context_and_retrieves_imported_resource(
    offline_resource_api, service, monkeypatch
):
    client, root_uri = offline_resource_api
    expanded_query = "OAuth authorization code authentication documentation"
    prompts = []

    class RecordingPlanner:
        async def get_completion_async(self, prompt):
            prompts.append(prompt)
            return json.dumps(
                {"queries": [{"query": expanded_query, "context_type": "resource", "priority": 1}]}
            )

    monkeypatch.setattr(
        service.viking_fs._vlm_resolver,
        "get_query_planner",
        AsyncMock(return_value=RecordingPlanner()),
    )
    embedded_queries = []
    provider = service.viking_fs._embedding_provider
    original_embed = provider.embed

    async def record_embed(account_id, content, *, is_query=False):
        if is_query:
            embedded_queries.append(content)
        return await original_embed(account_id, content, is_query=is_query)

    monkeypatch.setattr(provider, "embed", record_embed)

    response = await client.post("/api/v1/sessions", json={})
    assert response.status_code == 200, response.text
    session_id = response.json()["result"]["session_id"]
    response = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I am implementing OAuth authorization code authentication.",
        },
    )
    assert response.status_code == 200, response.text

    payload = {
        "query": "authentication",
        "session_id": session_id,
        "target_uri": root_uri,
        "level": [2],
    }
    expected_uris = [f"{root_uri}/oauth.md"]
    session_factory = Mock(wraps=service.sessions.session)
    monkeypatch.setattr(service.sessions, "session", session_factory)
    with monkeypatch.context() as disabled:
        disabled.setattr(service.viking_fs.retrieval_config, "enable_intent", False)
        response = await client.post("/api/v1/search/search", json=payload)

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert [item["uri"] for item in result["resources"]] == expected_uris
    assert "query_plan" not in result
    assert embedded_queries == ["authentication"]
    assert prompts == []
    session_factory.assert_not_called()

    embedded_queries.clear()
    response = await client.post("/api/v1/search/search", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert prompts
    assert any(
        "[user]: I am implementing OAuth authorization code authentication." in prompt
        for prompt in prompts
    )
    assert expanded_query in embedded_queries
    assert body["result"]["query_plan"]["queries"][0]["query"] == expanded_query
    resources = body["result"]["resources"]
    assert [item["uri"] for item in resources] == expected_uris
    assert all(item["level"] == 2 for item in resources)
