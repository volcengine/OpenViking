# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for session endpoints."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from openviking.message import Message
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.fixture(autouse=True)
def _configure_test_env(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(tmp_path / "workspace"),
                    "agfs": {"backend": "local", "mode": "binding-client"},
                    "vectordb": {"backend": "local"},
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "test-embedder",
                        "api_base": "http://127.0.0.1:11434/v1",
                        "dimension": 1024,
                    }
                },
                "encryption": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )

    mock_agfs = MockLocalAGFS(root_path=tmp_path / "mock_agfs_root")

    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    OpenVikingConfigSingleton.reset_instance()

    with patch("openviking.utils.agfs_utils.create_agfs_client", return_value=mock_agfs):
        yield

    OpenVikingConfigSingleton.reset_instance()


async def _wait_for_task(client: httpx.AsyncClient, task_id: str, timeout: float = 10.0):
    for _ in range(int(timeout / 0.1)):
        resp = await client.get(f"/api/v1/tasks/{task_id}")
        if resp.status_code == 200:
            task = resp.json()["result"]
            if task["status"] in ("completed", "failed"):
                return task
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


async def test_create_session(client: httpx.AsyncClient):
    resp = await client.post("/api/v1/sessions", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "session_id" in body["result"]


async def test_list_sessions(client: httpx.AsyncClient):
    # Create a session first
    await client.post("/api/v1/sessions", json={})
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["result"], list)


async def test_get_session(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["session_id"] == session_id


async def test_get_session_context(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Current live message"},
    )

    resp = await client.get(f"/api/v1/sessions/{session_id}/context")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["latest_archive_overview"] == ""
    assert body["result"]["pre_archive_abstracts"] == []
    assert [m["parts"][0]["text"] for m in body["result"]["messages"]] == ["Current live message"]


async def test_get_session_context_includes_incomplete_archive_messages(
    client: httpx.AsyncClient, service
):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Archived seed"},
    )
    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert commit_resp.status_code == 200

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    pending_messages = [
        Message.create_user("Pending user message"),
        Message.create_assistant("Pending assistant response"),
    ]
    await session._viking_fs.write_file(
        uri=f"{session.uri}/history/archive_002/messages.jsonl",
        content="\n".join(msg.to_jsonl() for msg in pending_messages) + "\n",
        ctx=session.ctx,
    )

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Current live message"},
    )

    resp = await client.get(f"/api/v1/sessions/{session_id}/context")
    assert resp.status_code == 200
    body = resp.json()
    assert [m["parts"][0]["text"] for m in body["result"]["messages"]] == [
        "Pending user message",
        "Pending assistant response",
        "Current live message",
    ]


async def test_add_message(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Hello, world!"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["message_count"] == 1


async def test_add_multiple_messages(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    # Add messages one by one; each add_message call should see
    # the accumulated count (messages are loaded from storage each time)
    resp1 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message 0"},
    )
    assert resp1.json()["result"]["message_count"] >= 1

    resp2 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message 1"},
    )
    count2 = resp2.json()["result"]["message_count"]

    resp3 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message 2"},
    )
    count3 = resp3.json()["result"]["message_count"]

    # Each add should increase the count
    assert count3 >= count2


async def test_add_message_persistence_regression(client: httpx.AsyncClient, service):
    """Regression: message payload must persist as valid parts across loads."""
    create_resp = await client.post("/api/v1/sessions", json={"user": "test"})
    session_id = create_resp.json()["result"]["session_id"]

    resp1 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message A"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["result"]["message_count"] == 1

    resp2 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message B"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["result"]["message_count"] == 2

    # Re-load through API path to ensure session file can be parsed back.
    get_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["result"]["message_count"] == 2

    # Verify stored message content survives load/decode.
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    assert len(session.messages) == 2
    assert session.messages[0].content == "Message A"
    assert session.messages[1].content == "Message B"


async def test_delete_session(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    # Add a message so the session file exists in storage
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "ensure persisted"},
    )
    # Compress to persist
    await client.post(f"/api/v1/sessions/{session_id}/commit")

    resp = await client.delete(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_compress_session(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    # Add some messages before committing
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Hello"},
    )

    # Default wait=False: returns accepted with task_id
    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "accepted"
    assert "usage" not in body
    assert "telemetry" not in body


async def test_extract_session_jsonable_regression(client: httpx.AsyncClient, service, monkeypatch):
    """Regression: extract endpoint should serialize internal objects."""

    class FakeMemory:
        __slots__ = ("uri",)

        def __init__(self, uri: str):
            self.uri = uri

        def to_dict(self):
            return {"uri": self.uri}

    async def fake_extract(_session_id: str, _ctx):
        return [FakeMemory("viking://user/memories/mock.md")]

    monkeypatch.setattr(service.sessions, "extract", fake_extract)

    create_resp = await client.post("/api/v1/sessions", json={"user": "test"})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(f"/api/v1/sessions/{session_id}/extract")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == [{"uri": "viking://user/memories/mock.md"}]


async def test_get_session_context_endpoint_returns_trimmed_latest_archive_and_messages(
    client: httpx.AsyncClient,
):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "archived message"},
    )
    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    task_id = commit_resp.json()["result"]["task_id"]
    await _wait_for_task(client, task_id)

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={
            "role": "assistant",
            "parts": [
                {"type": "text", "text": "Running tool"},
                {
                    "type": "tool",
                    "tool_id": "tool_123",
                    "tool_name": "demo_tool",
                    "tool_uri": f"viking://session/{session_id}/tools/tool_123",
                    "tool_input": {"x": 1},
                    "tool_status": "running",
                },
            ],
        },
    )

    resp = await client.get(f"/api/v1/sessions/{session_id}/context?token_budget=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"

    result = body["result"]
    assert result["latest_archive_overview"] == ""
    assert result["pre_archive_abstracts"] == []
    assert len(result["messages"]) == 1
    assert result["messages"][0]["role"] == "assistant"
    assert any(
        part["type"] == "tool" and part["tool_id"] == "tool_123"
        for part in result["messages"][0]["parts"]
    )
    assert result["stats"]["totalArchives"] == 1
    assert result["stats"]["includedArchives"] == 0
    assert result["stats"]["droppedArchives"] == 1
    assert result["stats"]["failedArchives"] == 0


async def test_get_session_archive_endpoint_returns_archive_details(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "archived question"},
    )
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "assistant", "content": "archived answer"},
    )
    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    task_id = commit_resp.json()["result"]["task_id"]
    await _wait_for_task(client, task_id)

    resp = await client.get(f"/api/v1/sessions/{session_id}/archives/archive_001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["archive_id"] == "archive_001"
    assert body["result"]["overview"]
    assert body["result"]["abstract"]
    assert [m["parts"][0]["text"] for m in body["result"]["messages"]] == [
        "archived question",
        "archived answer",
    ]


async def test_commit_endpoint_rejects_after_failed_archive(
    client: httpx.AsyncClient,
    service,
):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    async def failing_extract(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("synthetic extraction failure")

    service.sessions._session_compressor.extract_long_term_memories = failing_extract

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "first round"},
    )
    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    task_id = commit_resp.json()["result"]["task_id"]
    task = await _wait_for_task(client, task_id)
    assert task["status"] == "failed"

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "second round"},
    )
    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")

    assert resp.status_code == 412
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "FAILED_PRECONDITION"
    assert "unresolved failed archive" in body["error"]["message"]
