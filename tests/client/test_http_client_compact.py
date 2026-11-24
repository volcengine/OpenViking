"""Request compatibility with servers that reject unknown optional fields."""

import json

import httpx
import pytest_asyncio

from openviking_cli.client.http import AsyncHTTPClient


@pytest_asyncio.fixture
async def client_requests(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(tmp_path / "missing.conf"))
    requests = []

    def handle(request):
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "ok", "result": {}})

    client = AsyncHTTPClient(url="http://localhost:1933")
    client._http = httpx.AsyncClient(
        base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
    )
    try:
        yield client, requests
    finally:
        await client.close()


async def test_find_omits_unset_optional_fields(client_requests):
    client, requests = client_requests
    await client.find("hello")
    await client.find("hello", options={"tags": ["team=search", "env=prod"], "level": 0})

    assert requests == [
        ("/api/v1/search/find", {"query": "hello", "target_uri": "", "limit": 10}),
        (
            "/api/v1/search/find",
            {
                "query": "hello",
                "target_uri": "",
                "limit": 10,
                "tags": ["team=search", "env=prod"],
                "level": 0,
            },
        ),
    ]


async def test_search_omits_unset_optional_fields(client_requests):
    client, requests = client_requests
    await client.search("hello")

    assert requests == [
        ("/api/v1/search/search", {"query": "hello", "target_uri": "", "limit": 10})
    ]


async def test_add_resource_omits_empty_args_and_null_fields(client_requests):
    client, requests = client_requests
    await client.add_resource("https://example.com/doc", options={"args": {}})
    args = {"feishu_access_token": "test-token", "parse_mode": "no_split"}
    await client.add_resource("https://example.feishu.cn/docx/example", options={"args": args})

    assert requests == [
        ("/api/v1/resources", {"path": "https://example.com/doc", "wait": False}),
        (
            "/api/v1/resources",
            {"path": "https://example.feishu.cn/docx/example", "wait": False, "args": args},
        ),
    ]
