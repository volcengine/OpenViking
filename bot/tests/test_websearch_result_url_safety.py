"""Tests for untrusted result URLs returned by web-search providers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from vikingbot.agent.tools.websearch.brave import BraveBackend
from vikingbot.agent.tools.websearch.ddgs import DDGSBackend
from vikingbot.agent.tools.websearch.exa import ExaBackend
from vikingbot.agent.tools.websearch.tavily import TavilyBackend
from vikingbot.agent.tools.websearch.url_safety import safe_public_result_url

PRIVATE_URLS = [
    "http://127.0.0.1/private",
    "http://localhost./private",
    "http://service.local/private",
    "http://metadata.google.internal/private",
    "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.1/private",
    "http://224.0.0.1/private",
    "http://240.0.0.1/private",
    "http://3758096385/private",
    "http://0x7f000001/private",
    "http://0177.0.0.1/private",
    "http://%31%32%37.0.0.1/private",
    "http://[::7f00:1]/private",
    "http://[::ffff:127.0.0.1]/private",
    "http://[64:ff9b::7f00:1]/private",
    "http://[2002:7f00:1::]/private",
    "http://127。0。0。1/private",
    "http://127.0.0.1\\@example.com/private",
    "file:///etc/passwd",
    "",
    "   ",
    None,
    {"url": "https://example.com"},
]
PUBLIC_URL = "https://example.com/keep"


@pytest.mark.parametrize("url", PRIVATE_URLS)
def test_safe_public_result_url_drops_non_public_targets(url):
    assert safe_public_result_url(url) == ""


@pytest.mark.parametrize(
    "url",
    [
        PUBLIC_URL,
        "http://8.8.8.8/page",
        "https://[2606:4700:4700::1111]/page",
        "  https://example.com/trimmed  ",
    ],
)
def test_safe_public_result_url_keeps_public_targets(url):
    assert safe_public_result_url(url) == url.strip()


def _http_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.get.return_value = response
    client.post.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = client
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_name", ["brave", "ddgs", "exa", "tavily"])
@pytest.mark.parametrize("include_public", [True, False])
async def test_websearch_backends_drop_non_public_result_urls(backend_name, include_public):
    rows = [
        {"title": "Private", "url": PRIVATE_URLS[4], "href": PRIVATE_URLS[4]},
    ]
    if include_public:
        rows.append(
            {
                "title": "Public",
                "url": "  https://example.com/keep  ",
                "href": "  https://example.com/keep  ",
                "description": "description",
                "text": "text",
                "content": "content",
            }
        )

    if backend_name == "brave":
        backend = BraveBackend(api_key="synthetic")
        with patch(
            "vikingbot.agent.tools.websearch.brave.httpx.AsyncClient",
            return_value=_http_response({"web": {"results": rows}}),
        ):
            output = await backend.search("query", 2)
    elif backend_name == "exa":
        backend = ExaBackend(api_key="synthetic")
        with patch(
            "vikingbot.agent.tools.websearch.exa.httpx.AsyncClient",
            return_value=_http_response({"results": rows}),
        ):
            output = await backend.search("query", 2)
    elif backend_name == "ddgs":
        backend = DDGSBackend()
        backend._ddgs = SimpleNamespace(text=lambda **_kwargs: rows)
        output = await backend.search("query", 2)
    else:
        backend = object.__new__(TavilyBackend)
        backend.api_key = "synthetic"
        backend._client = SimpleNamespace(search=AsyncMock(return_value={"results": rows}))
        output = await backend.search("query", 2)

    assert PRIVATE_URLS[4] not in output
    assert "Private" not in output
    if include_public:
        assert PUBLIC_URL in output
        assert f"{PUBLIC_URL}  " not in output
        assert "Public" in output
        assert "1. Public" in output
    else:
        assert output == "No results for: query"
