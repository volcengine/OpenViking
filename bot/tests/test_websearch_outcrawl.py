# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the Outcrawl web search backend."""

import json

import httpx
import pytest
from vikingbot.agent.tools.websearch import WebSearchTool
from vikingbot.agent.tools.websearch.outcrawl import OutcrawlBackend


@pytest.fixture(autouse=True)
def no_search_env(monkeypatch):
    for name in ("OUTCRAWL_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def mock_outcrawl(monkeypatch, handle):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(
        "vikingbot.agent.tools.websearch.outcrawl.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


async def test_outcrawl_search_formats_hits(monkeypatch):
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "url": "https://docs.python.org/3/library/asyncio-task.html",
                        "title": "Coroutines and tasks",
                        "snippet": "The asyncio.TaskGroup class provides a modern alternative.",
                        "rank": 1,
                    },
                    {"url": "https://example.com/b", "title": "Second", "snippet": "", "rank": 2},
                ],
                "usage": {"credits": "1"},
            },
        )

    mock_outcrawl(monkeypatch, handle)
    tool = WebSearchTool(backend="outcrawl", outcrawl_api_key="ock_test")

    result = await tool.execute(None, query="asyncio taskgroup", count=2)

    assert result == (
        "Results for: asyncio taskgroup\n\n"
        "1. Coroutines and tasks\n   https://docs.python.org/3/library/asyncio-task.html\n"
        "   The asyncio.TaskGroup class provides a modern alternative.\n"
        "2. Second\n   https://example.com/b"
    )
    assert str(requests[0].url) == "https://api.outcrawl.ai/v1/search"
    assert requests[0].headers["authorization"] == "Bearer ock_test"
    assert json.loads(requests[0].content) == {"query": "asyncio taskgroup", "limit": 2}


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"hits": [], "usage": {"credits": "0"}}, "No results for: q"),
        (401, {"code": "invalid_key", "message": "bad key"}, "Error: Client error '401"),
    ],
)
async def test_outcrawl_search_empty_and_errors(monkeypatch, status, body, expected):
    mock_outcrawl(monkeypatch, lambda request: httpx.Response(status, json=body))

    result = await OutcrawlBackend(api_key="ock_test").search("q", 5)

    assert result.startswith(expected)


async def test_outcrawl_requires_key_and_joins_auto_selection(monkeypatch):
    assert await OutcrawlBackend().search("q", 5) == "Error: OUTCRAWL_API_KEY not configured"

    monkeypatch.setenv("OUTCRAWL_API_KEY", "ock_env")
    assert WebSearchTool(backend="auto").backend.name == "outcrawl"
    assert WebSearchTool(backend="auto", tavily_api_key="tvly").backend.name == "tavily"
