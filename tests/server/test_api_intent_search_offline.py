# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Offline session-aware search through the real HTTP and retrieval stack.

Run: pytest tests/server/test_api_intent_search_offline.py
Only model calls are replaced; query planning and vector retrieval run normally.
"""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def sample_markdown_file(temp_dir):
    path = temp_dir / "oauth.md"
    path.write_text("# OAuth authentication\n\nUse OAuth authorization codes to authenticate.\n")
    return path


@pytest.fixture(autouse=True)
def offline_config(temp_dir, monkeypatch):
    config = {
        "enable_watch_scheduler": False,
        "storage": {
            "workspace": str(temp_dir / "data"),
            "agfs": {"backend": "local"},
            "vectordb": {"backend": "local"},
        },
        "embedding": {
            "dense": {
                "provider": "openai",
                "model": "offline-embedding",
                "api_key": "unused-offline-key",
                "dimension": 2048,
            }
        },
    }
    config_path = temp_dir / "ov.conf"
    config_path.write_text(json.dumps(config))
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(config_path=str(config_path))
    yield
    OpenVikingConfigSingleton.reset_instance()


@pytest.fixture(autouse=True)
def reject_external_http(monkeypatch):
    def reject_request(*args, **kwargs):
        pytest.fail("Offline intent search must not send an external HTTP request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject_request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_request)


@pytest_asyncio.fixture
async def client_with_resource(client, sample_markdown_file, upload_temp_dir):
    response = await client.post(
        "/api/v1/resources",
        json={"temp_file_id": sample_markdown_file.name, "wait": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok", body
    root_uri = body["result"]["root_uri"]
    assert root_uri, body
    return client, root_uri


async def test_search_expands_session_context_and_retrieves_imported_resource(
    client_with_resource, service, monkeypatch
):
    client, root_uri = client_with_resource
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

    response = await client.post(
        "/api/v1/search/search",
        json={
            "query": "authentication",
            "session_id": session_id,
            "target_uri": root_uri,
            "level": [2],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert len(prompts) == 1
    assert "[user]: I am implementing OAuth authorization code authentication." in prompts[0]
    assert expanded_query in embedded_queries
    assert body["result"]["query_plan"]["queries"][0]["query"] == expanded_query
    resources = body["result"]["resources"]
    assert any(
        item["uri"] == root_uri or item["uri"].startswith(root_uri.rstrip("/") + "/")
        for item in resources
    ), resources
    assert all(item["level"] == 2 for item in resources)
    assert all(0 <= item["score"] <= 1 for item in resources)
