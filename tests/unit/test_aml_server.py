"""Competition API contract through the real OpenViking SDK request builder."""

import asyncio
import json

import httpx
import pytest

from benchmark.aml import server
from benchmark.aml.server import AMLSettings, create_app

IMAGES = ["data:image/png;base64,YQ==", "data:image/png;base64,Yg=="]


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
        assert body.get("image_url") == (IMAGES if user == "bob" else None)
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
                json={
                    "user_id": "bob",
                    "query": [
                        {"type": "image_url", "image_url": {"url": IMAGES[0]}},
                        {"type": "text", "text": "Which?"},
                        {"type": "image_url", "image_url": {"url": IMAGES[1]}},
                        {"type": "text", "text": "Compare both."},
                    ],
                    "options": ["A", "B"],
                    "top_k": 2,
                },
            ),
        )
    assert sorted(seen) == [
        ("alice", "Where?"),
        ("bob", "Which?\nCompare both.\n\nAnswer options:\n1. A\n2. B"),
    ]
    for user, response in zip(("alice", "bob"), responses, strict=True):
        assert response.status_code == 200
        assert response.json() == {
            "data": [
                {"id": f"viking://user/{user}/memories/a", "content": "body"},
                {"id": f"viking://user/{user}/memories/b", "content": "summary"},
            ]
        }


async def test_add_commits_native_parts_and_image_search_retries(adapter_client, monkeypatch):
    requests = []
    parts = [
        {"type": "text", "text": "Remember these receipts."},
        {"type": "image_url", "image_url": {"url": IMAGES[0]}},
        {"type": "image_url", "image_url": {"url": IMAGES[1]}},
    ]
    payload = {
        "request_id": "request-1",
        "user_id": "alice",
        "session_id": "session-1",
        "messages": [
            {"role": "user", "content": parts, "timestamp": 0},
            {"role": "assistant", "content": "  Saved.\n"},
        ],
    }

    def handle(request):
        requests.append(request)
        assert request.headers["X-OpenViking-User"] == "alice"
        assert request.headers["X-OpenViking-Account"] == "competition"
        assert request.headers["X-API-Key"] == "test-ov-key"
        if request.url.path == "/api/v1/sessions/session-1/messages/batch":
            assert json.loads(request.content) == {
                "messages": [
                    {"role": "user", "parts": parts, "created_at": "1970-01-01T00:00:00.000Z"},
                    {"role": "assistant", "content": "  Saved.\n"},
                ]
            }
            result = {"added": 2}
        elif request.url.path == "/api/v1/sessions/session-1/commit":
            assert json.loads(request.content) == {"keep_recent_count": 0}
            result = {"status": "accepted", "task_id": "task-1"}
        elif request.url.path == "/api/v1/tasks/task-1":
            assert request.method == "GET"
            result = {"status": "completed"}
        else:
            assert request.url.path == "/api/v1/search/find"
            if len(requests) == 4:
                raise httpx.ReadTimeout("temporary failure", request=request)
            result = {"memories": []}
        return httpx.Response(200, json={"status": "ok", "result": result})

    async with adapter_client(handle) as client:
        # Each image fits; the aggregate budget is enforced before any write.
        monkeypatch.setattr(server, "MAX_ADD_IMAGE_BYTES", 1)
        rejected = await client.post("/add", json=payload)
        assert rejected.status_code == 422
        assert requests == []
        monkeypatch.setattr(server, "MAX_ADD_IMAGE_BYTES", 2)
        added = await client.post("/add", json=payload)
        assert added.status_code == 200
        assert added.json() == {
            "success": True,
            "request_id": "request-1",
            "user_id": "alice",
            "session_id": "session-1",
        }
        assert [request.url.path for request in requests] == [
            "/api/v1/sessions/session-1/messages/batch",
            "/api/v1/sessions/session-1/commit",
            "/api/v1/tasks/task-1",
        ]
        response = await client.post(
            "/search", json={"user_id": "alice", "query": [parts[1]], "top_k": 2}
        )
    assert response.status_code == 200
    assert response.json() == {"data": []}
    assert len(requests) == 5
    assert json.loads(requests[-1].content)["query"] == ""
    assert json.loads(requests[-1].content)["image_url"] == IMAGES[0]
    assert requests[-2].content == requests[-1].content
    assert requests[-2].headers == requests[-1].headers
