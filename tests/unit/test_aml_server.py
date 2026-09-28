"""Competition API contract through the real OpenViking SDK request builder."""

import asyncio
import json

import httpx
import pytest

from benchmark.aml.server import AMLSettings, create_app


@pytest.fixture
def adapter_client(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(tmp_path / "missing.conf"))
    client_type = httpx.AsyncClient

    def build(handler):
        def sdk_client(**kwargs):
            return client_type(**kwargs, transport=httpx.MockTransport(handler))

        monkeypatch.setattr("openviking_sdk.client.httpx.AsyncClient", sdk_client)
        app = create_app(
            settings=AMLSettings(
                openviking_url="http://ov.test",
                openviking_account="competition",
                openviking_api_key="test-ov-key",
                aml_api_key="test-aml-key",
                retry_delay_seconds=0.001,
            )
        )
        return client_type(
            transport=httpx.ASGITransport(app=app),
            base_url="http://adapter.test",
            headers={"Authorization": "Token test-aml-key"},
        )

    return build


async def test_search_uses_native_find_and_preserves_user_scope_and_aml_response(adapter_client):
    seen = []

    async def handle(request):
        assert request.method == "POST"
        assert request.url.path == "/api/v1/search/find"
        assert request.headers["X-OpenViking-Account"] == "competition"
        assert request.headers["X-API-Key"] == "test-ov-key"
        user = request.headers["X-OpenViking-User"]
        body = json.loads(request.content)
        assert body["target_uri"] == "viking://~/memories"
        assert body["context_type"] == ["memory"]
        assert body["level"] == 2
        assert body["limit"] == 2
        assert "mode" not in body
        assert not body.get("session_id")
        assert not body.get("read_content")
        seen.append((user, body["query"]))
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {
                    "memories": [
                        {"uri": f"viking://user/{user}/memories/a", "content": "body"},
                        {"uri": f"viking://user/{user}/memories/b", "abstract": "summary"},
                        {"uri": f"viking://user/{user}/memories/c", "abstract": "extra"},
                    ],
                    "resources": [{"uri": "viking://resources/other", "content": "other"}],
                    "skills": [],
                },
            },
        )

    async with adapter_client(handle) as client:
        responses = await asyncio.gather(
            client.post("/search", json={"user_id": "alice", "query": "Where?", "top_k": 2}),
            client.post(
                "/search",
                json={"user_id": "bob", "query": "Which?", "options": ["A", "B"], "top_k": 2},
            ),
        )
    assert sorted(seen) == [("alice", "Where?"), ("bob", "Which?\n\nAnswer options:\n1. A\n2. B")]
    for user, response in zip(("alice", "bob"), responses, strict=True):
        assert response.status_code == 200
        assert response.json() == {
            "data": [
                {"id": f"viking://user/{user}/memories/a", "content": "body"},
                {"id": f"viking://user/{user}/memories/b", "content": "summary"},
            ]
        }


async def test_search_retries_transient_native_find_failure(adapter_client):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("temporary failure", request=request)
        return httpx.Response(200, json={"status": "ok", "result": {"memories": []}})

    async with adapter_client(handle) as client:
        response = await client.post(
            "/search", json={"user_id": "alice", "query": "Where?", "top_k": 2}
        )
    assert response.status_code == 200
    assert response.json() == {"data": []}
    assert len(requests) == 2
    assert all(request.url.path == "/api/v1/search/find" for request in requests)
    assert requests[0].content == requests[1].content
    assert requests[0].headers == requests[1].headers
