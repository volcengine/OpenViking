"""Tests for the Firecrawl web search backend."""

import httpx
import pytest
from vikingbot.agent.tools.websearch import WebSearchTool


@pytest.fixture(autouse=True)
def _no_search_keys(monkeypatch):
    for name in ("FIRECRAWL_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _web(*items):
    return httpx.Response(200, json={"success": True, "data": {"web": list(items)}})


def _timeout(request):
    raise httpx.ReadTimeout("", request=request)


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (
            lambda request: _web(
                {"title": "OpenViking", "url": "https://openviking.ai", "description": "x" * 600},
                {"title": "Docs", "url": "https://docs.openviking.ai", "description": "Guides"},
            ),
            "Results for: openviking\n\n"
            f"1. OpenViking\n   https://openviking.ai\n   {'x' * 500}...\n"
            "2. Docs\n   https://docs.openviking.ai\n   Guides",
        ),
        (lambda request: _web(), "No results for: openviking"),
        (
            lambda request: httpx.Response(402, json={"error": "Insufficient credits"}),
            "Error: Firecrawl search failed (402): Insufficient credits",
        ),
        (_timeout, "Error: ReadTimeout"),
    ],
)
async def test_firecrawl_search_output(monkeypatch, handler, expected):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "vikingbot.agent.tools.websearch.firecrawl.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    tool = WebSearchTool(firecrawl_api_key="fc-test")

    assert await tool.execute(None, "openviking", 2) == expected


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ({"firecrawl_api_key": "fc-test"}, "firecrawl"),
        ({"firecrawl_api_key": "fc-test", "brave_api_key": "brave-test"}, "firecrawl"),
        ({"firecrawl_api_key": "fc-test", "tavily_api_key": "tvly-test"}, "tavily"),
        ({"firecrawl_api_key": "fc-test", "exa_api_key": "exa-test"}, "exa"),
        ({}, "ddgs"),
    ],
)
def test_auto_backend_priority(keys, expected):
    assert WebSearchTool(**keys).backend.name == expected
