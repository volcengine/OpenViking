# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the OpenWebUI tool server.

Each test mocks the OpenViking HTTP layer with respx and asserts that the
matching tool route forwards the right method, path, body, and tenant
headers, and returns a payload matching its Pydantic model.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from openviking_openwebui.config import Settings
from openviking_openwebui.server import create_app

SETTINGS = Settings(
    endpoint="http://ov.test",
    api_key="key-xyz",
    account="acct",
    user="alice",
    agent="webui",
    bind="0.0.0.0:8765",
    timeout_seconds=5.0,
)


@pytest.fixture
async def client():
    app = create_app(SETTINGS)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


def _assert_headers(request: httpx.Request) -> None:
    assert request.headers["authorization"] == "Bearer key-xyz"
    assert request.headers["x-openviking-account"] == "acct"
    assert request.headers["x-openviking-user"] == "alice"
    assert request.headers["x-openviking-actor-peer"] == "webui"


@respx.mock
async def test_ov_search_calls_find(client: httpx.AsyncClient) -> None:
    route = respx.post("http://ov.test/api/v1/search/find").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {
                    "memories": [
                        {"uri": "viking://user/memories/a.md", "score": 0.9, "snippet": "hi"}
                    ],
                    "resources": [],
                },
            },
        )
    )
    resp = await client.post("/tools/ov_search", json={"query": "hello", "limit": 5})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["hits"][0]["uri"] == "viking://user/memories/a.md"
    assert data["hits"][0]["score"] == pytest.approx(0.9)
    assert route.called
    sent = route.calls.last.request
    _assert_headers(sent)
    assert b'"query":"hello"' in route.calls.last.request.content.replace(b" ", b"")


@respx.mock
async def test_ov_recall_scopes_to_memories(client: httpx.AsyncClient) -> None:
    route = respx.post("http://ov.test/api/v1/search/find").mock(
        return_value=httpx.Response(200, json={"result": {"memories": []}})
    )
    resp = await client.post("/tools/ov_recall_memories", json={"query": "prefs", "limit": 3})
    assert resp.status_code == 200
    sent = route.calls.last.request
    _assert_headers(sent)
    raw = sent.content.decode()
    assert "viking://user/memories/" in raw
    assert '"limit":3' in raw.replace(" ", "")


@respx.mock
async def test_ov_add_memory_writes_under_memories(client: httpx.AsyncClient) -> None:
    route = respx.post("http://ov.test/api/v1/content/write").mock(
        return_value=httpx.Response(200, json={"status": "ok", "result": {"bytes": 10}})
    )
    resp = await client.post(
        "/tools/ov_add_memory",
        json={"name": "profile.md", "content": "I like Rust", "mode": "replace"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["uri"] == "viking://user/memories/profile.md"
    sent = route.calls.last.request
    _assert_headers(sent)
    raw = sent.content.decode()
    assert "viking://user/memories/profile.md" in raw
    assert "I like Rust" in raw


@respx.mock
async def test_ov_list_memories_calls_fs_ls(client: httpx.AsyncClient) -> None:
    route = respx.get("http://ov.test/api/v1/fs/ls").mock(
        return_value=httpx.Response(200, json={"status": "ok", "result": {"entries": []}})
    )
    resp = await client.post("/tools/ov_list_memories", json={"recursive": True, "limit": 50})
    assert resp.status_code == 200
    sent = route.calls.last.request
    _assert_headers(sent)
    assert "uri=viking%3A%2F%2Fuser%2Fmemories%2F" in str(sent.url)
    assert "recursive=true" in str(sent.url)
    assert "node_limit=50" in str(sent.url)


@respx.mock
async def test_ov_read_resource_calls_content_read(client: httpx.AsyncClient) -> None:
    route = respx.get("http://ov.test/api/v1/content/read").mock(
        return_value=httpx.Response(200, json={"status": "ok", "result": "body"})
    )
    resp = await client.post(
        "/tools/ov_read_resource",
        json={"uri": "viking://resources/a.md", "offset": 0, "limit": -1},
    )
    assert resp.status_code == 200
    sent = route.calls.last.request
    _assert_headers(sent)
    assert "uri=viking%3A%2F%2Fresources%2Fa.md" in str(sent.url)


@respx.mock
async def test_ov_add_resource_posts_resources(client: httpx.AsyncClient) -> None:
    route = respx.post("http://ov.test/api/v1/resources").mock(
        return_value=httpx.Response(
            200, json={"status": "ok", "result": {"root_uri": "viking://x"}}
        )
    )
    resp = await client.post(
        "/tools/ov_add_resource",
        json={"path": "https://example.com/doc.md", "wait": True},
    )
    assert resp.status_code == 200
    sent = route.calls.last.request
    _assert_headers(sent)
    raw = sent.content.decode()
    assert "https://example.com/doc.md" in raw
    assert '"wait":true' in raw.replace(" ", "")


@respx.mock
async def test_ov_session_status_gets_session(client: httpx.AsyncClient) -> None:
    route = respx.get("http://ov.test/api/v1/sessions/sess-42").mock(
        return_value=httpx.Response(200, json={"status": "ok", "result": {"session_id": "sess-42"}})
    )
    resp = await client.post("/tools/ov_session_status", json={"session_id": "sess-42"})
    assert resp.status_code == 200
    sent = route.calls.last.request
    _assert_headers(sent)
    assert sent.url.path == "/api/v1/sessions/sess-42"


@respx.mock
async def test_error_pass_through(client: httpx.AsyncClient) -> None:
    respx.post("http://ov.test/api/v1/search/find").mock(
        return_value=httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "nope"}})
    )
    resp = await client.post("/tools/ov_search", json={"query": "x"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error"]["code"] == "NOT_FOUND"


async def test_openapi_lists_seven_tools(client: httpx.AsyncClient) -> None:
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    op_ids = {
        op.get("operationId")
        for path in spec["paths"].values()
        for op in path.values()
        if isinstance(op, dict)
    }
    expected = {
        "ov_search",
        "ov_recall_memories",
        "ov_add_memory",
        "ov_list_memories",
        "ov_read_resource",
        "ov_add_resource",
        "ov_session_status",
    }
    assert expected.issubset(op_ids)
