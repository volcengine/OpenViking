# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the VikingBot web_search tool."""

import json

import httpx
import pytest
from vikingbot.agent.tools.factory import register_default_tools, register_subagent_tools
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.config import loader
from vikingbot.config.schema import SessionKey

QUERY = "What changed in battery recycling?"
SESSION = SessionKey(type="test", channel_id="test", chat_id="search")


@pytest.fixture
def search_config(tmp_path, monkeypatch):
    path = tmp_path / "ov.conf"
    monkeypatch.setattr(loader, "CONFIG_PATH", path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("BRAVE_API_KEY", "brave-test-key")
    monkeypatch.setenv("WEBZ_API_TOKEN", "webz-test-token")

    def load(**search):
        path.write_text(json.dumps({"bot": {"tools": {"web": {"search": search}}}}))
        return loader.load_config()

    return load


@pytest.fixture
def search_http(monkeypatch):
    client = httpx.AsyncClient

    def install(handler):
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda: client(transport=httpx.MockTransport(handler))
        )

    return install


@pytest.mark.asyncio
async def test_configured_webz_search_returns_news(search_config, search_http):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.host == "api.search.brave.com":
            return httpx.Response(
                200, json={"web": {"results": [{"title": "Battery recycling encyclopedia"}]}}
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "article": {
                            "title": "New battery recycling plant opens",
                            "url": "https://news.example.com/battery-plant",
                            "published_at": "2026-11-22T09:30:00Z",
                        },
                        "chunk": {"text": "The plant started processing batteries on Friday."},
                    },
                    {"article": {"title": "Second story"}},
                ]
            },
        )

    search_http(respond)
    registry = ToolRegistry()
    register_default_tools(registry, search_config(backend="webz"), include_viking_tools=False)
    filters = {
        "language": ["english"],
        "country": ["US"],
        "published_from": "2026-11-20",
        "published_to": "2026-11-23",
        "sentiment": ["positive"],
        "domain": ["news.example.com"],
        "ticker": ["EXAMPLE"],
    }
    params = {"query": QUERY, "count": 1, "filters": filters}

    result = await registry.execute("web_search", params, SESSION)

    assert result == (
        f"Results for: {QUERY}\n\n"
        "1. New battery recycling plant opens\n"
        "   https://news.example.com/battery-plant\n"
        "   Published: 2026-11-22T09:30:00Z\n"
        "   The plant started processing batteries on Friday."
    )
    definition = next(
        d["function"] for d in registry.get_definitions() if d["function"]["name"] == "web_search"
    )
    assert set(filters) <= definition["parameters"]["properties"]["filters"]["properties"].keys()
    (request,) = requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.webz.io/api/news/context"
    assert request.headers["Authorization"] == "Bearer webz-test-token"
    assert json.loads(request.content) == {"query": QUERY, "k": 1, "filters": filters}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, "Error: WEBZ_API_TOKEN not configured"),
        (401, "Error: Webz.io returned HTTP 401"),
        (200, f"No results for: {QUERY}"),
    ],
    ids=["missing-token", "unauthorized", "no-results"],
)
async def test_webz_subagent_search_result(
    search_config, search_http, monkeypatch, status, expected
):
    if status is None:
        monkeypatch.delenv("WEBZ_API_TOKEN")

    def respond(request):
        assert status is not None
        assert json.loads(request.content) == {"query": QUERY, "k": 5}
        return httpx.Response(status, json={"results": []})

    search_http(respond)
    registry = ToolRegistry()
    register_subagent_tools(registry, search_config(backend="webz"))

    assert await registry.execute("web_search", {"query": QUERY}, SESSION) == expected


@pytest.mark.asyncio
async def test_default_subagent_search_still_uses_brave(search_config, search_http):
    def respond(request):
        assert request.url.host == "api.search.brave.com"
        assert request.url.params["count"] == "5"
        return httpx.Response(
            200,
            json={
                "web": {"results": [{"title": "Recycling guide", "url": "https://example.com/"}]}
            },
        )

    search_http(respond)
    registry = ToolRegistry()
    register_subagent_tools(registry, search_config())
    result = await registry.execute("web_search", {"query": QUERY}, SESSION)

    assert result == f"Results for: {QUERY}\n\n1. Recycling guide\n   https://example.com/"
