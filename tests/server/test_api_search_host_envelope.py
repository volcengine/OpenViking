# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Host-envelope normalization of search queries."""

import httpx
import pytest

from openviking.utils.host_envelope import BUILTIN_ENVELOPES
from openviking_cli.retrieve import FindResult

WRAPPED = "<system-reminder>\nTodo list is empty\n</system-reminder>\nwhy is recall slow?"
NOTICE = "<task-notification>\n<status>completed</status>\n</task-notification>"


@pytest.fixture
def captured(app, service, monkeypatch):
    app.state.search_host_envelopes = (BUILTIN_ENVELOPES["claude-code"],)
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs["query"])
        return FindResult(memories=[], resources=[], skills=[])

    monkeypatch.setattr(service.search, "find", fake)
    monkeypatch.setattr(service.search, "search", fake)
    return calls


@pytest.mark.parametrize("path", ["/api/v1/search/find", "/api/v1/search/search"])
async def test_query_is_stripped_before_search(client: httpx.AsyncClient, captured, path):
    response = await client.post(path, json={"query": WRAPPED})

    assert response.status_code == 200
    assert captured == ["why is recall slow?"]


@pytest.mark.parametrize("path", ["/api/v1/search/find", "/api/v1/search/search"])
async def test_host_only_query_returns_empty_without_searching(
    client: httpx.AsyncClient, captured, path
):
    response = await client.post(path, json={"query": NOTICE})

    assert response.status_code == 200
    assert response.json()["result"]["total"] == 0
    assert captured == []


@pytest.mark.parametrize("path", ["/api/v1/search/search", "/api/v1/search/recall"])
async def test_host_only_query_returns_empty_context(client: httpx.AsyncClient, captured, path):
    payload = {"query": NOTICE}
    if path.endswith("/search"):
        payload["mode"] = "context"
    response = await client.post(path, json=payload)

    assert response.status_code == 200
    result = response.json()["result"]
    assert (result["entries"], result["stats"]["skipped"]) == ([], "host_envelope_only")


async def test_short_query_without_envelope_is_searched(client: httpx.AsyncClient, captured):
    response = await client.post("/api/v1/search/find", json={"query": "r7"})

    assert response.status_code == 200
    assert captured == ["r7"]
