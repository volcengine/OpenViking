# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for session endpoints."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from openviking.message import Message
from openviking.server.api_keys import APIKeyManager
from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import sessions as sessions_router
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.utils.mock_agfs import MockLocalAGFS

DEFAULT_USER = UserIdentifier.the_default_user()
TEST_ROOT_KEY = "root-secret-key-for-session-tests"
_UNSET = object()


def _message_request(
    role: str,
    *,
    content: str | None = None,
    parts: list[dict] | None = None,
    role_id: object = _UNSET,
) -> dict:
    payload = {"role": role}
    if content is not None:
        payload["content"] = content
    if parts is not None:
        payload["parts"] = parts
    if role_id is _UNSET and role == "user":
        payload["role_id"] = DEFAULT_USER.user_id
    elif role_id is _UNSET and role == "assistant":
        payload["role_id"] = DEFAULT_USER.agent_id
    elif role_id is not None:
        payload["role_id"] = role_id
    return payload


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

    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
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


def _session_route_request(
    *,
    auth_mode: str = "api_key",
    api_key_manager=None,
) -> Request:
    app = FastAPI()
    app.state.config = ServerConfig(auth_mode=auth_mode)
    app.state.api_key_manager = api_key_manager
    scope = {
        "type": "http",
        "path": "/api/v1/sessions/test-session/messages",
        "headers": [],
        "app": app,
    }
    return Request(scope)


async def _call_add_message_route(
    service,
    monkeypatch,
    *,
    ctx: RequestContext,
    payload: dict,
    session_id: str = "test-session",
):
    monkeypatch.setattr(sessions_router, "get_service", lambda: service)
    return await sessions_router.add_message(
        request=sessions_router.AddMessageRequest.model_validate(payload),
        session_id=session_id,
        _ctx=ctx,
    )


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
        json=_message_request("user", content="Current live message"),
    )

    resp = await client.get(f"/api/v1/sessions/{session_id}/context")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["latest_archive_overview"] == ""
    assert body["result"]["pre_archive_abstracts"] == []
    assert [m["parts"][0]["text"] for m in body["result"]["messages"]] == ["Current live message"]


async def test_get_session_context_rejects_negative_token_budget(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/sessions/any-session/context?token_budget=-1")

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert body["error"]["details"] == {"field": "token_budget", "value": -1}


async def test_get_session_context_includes_incomplete_archive_messages(
    client: httpx.AsyncClient, service
):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="Archived seed"),
    )
    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert commit_resp.status_code == 200

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    pending_messages = [
        Message.create_user("Pending user message", role_id=DEFAULT_USER.user_id),
        Message.create_assistant(
            "Pending assistant response",
            role_id=DEFAULT_USER.agent_id,
        ),
    ]
    await session._viking_fs.write_file(
        uri=f"{session.uri}/history/archive_002/messages.jsonl",
        content="\n".join(msg.to_jsonl() for msg in pending_messages) + "\n",
        ctx=session.ctx,
    )

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="Current live message"),
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
        json=_message_request("user", content="Hello, world!"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["message_count"] == 1


async def test_add_message_root_request_autofills_role_id(service, monkeypatch):
    session_id = "root-auto-fill"
    ctx = RequestContext(user=DEFAULT_USER, role=Role.ROOT)

    response = await _call_add_message_route(
        service,
        monkeypatch,
        ctx=ctx,
        payload=_message_request("user", content="hello root", role_id=None),
        session_id=session_id,
    )

    assert response.result["message_count"] == 1
    session = await service.sessions.get(session_id, ctx, auto_create=False)
    await session.load()
    assert session.messages[-1].role_id == DEFAULT_USER.user_id


async def test_add_message_trusted_request_allows_explicit_role_id(service, monkeypatch):
    session_id = "trusted-explicit-role-id"
    ctx = RequestContext(
        user=UserIdentifier("acct_trusted", "caller", "assistant-a"),
        role=Role.USER,
    )

    response = await _call_add_message_route(
        service,
        monkeypatch,
        ctx=ctx,
        payload=_message_request("assistant", content="hello trusted", role_id="assistant-b"),
        session_id=session_id,
    )

    assert response.result["message_count"] == 1
    session = await service.sessions.get(session_id, ctx, auto_create=False)
    await session.load()
    assert session.messages[-1].role_id == "assistant-b"


async def test_add_message_admin_request_allows_registered_user_role_id(service, monkeypatch):
    manager = APIKeyManager(root_key=TEST_ROOT_KEY, viking_fs=service.viking_fs)
    await manager.load()
    account_id = "acct_session_admin"
    await manager.create_account(account_id, "admin_user")
    await manager.register_user(account_id, "alice")

    ctx = RequestContext(
        user=UserIdentifier(account_id, "admin_user", "assistant-admin"),
        role=Role.ADMIN,
    )
    session_id = "admin-explicit-role-id"

    response = await _call_add_message_route(
        service,
        monkeypatch,
        ctx=ctx,
        payload=_message_request("user", content="hello admin", role_id="alice"),
        session_id=session_id,
    )

    assert response.result["message_count"] == 1
    session = await service.sessions.get(session_id, ctx, auto_create=False)
    await session.load()
    assert session.messages[-1].role_id == "alice"


async def test_add_message_user_request_allows_explicit_role_id(service, monkeypatch):
    session_id = "user-explicit-role-id"
    ctx = RequestContext(
        user=UserIdentifier("acct_session_user", "alice", "assistant-user"),
        role=Role.USER,
    )

    response = await _call_add_message_route(
        service,
        monkeypatch,
        ctx=ctx,
        payload=_message_request("user", content="hello user", role_id="wx/user-01@abc"),
        session_id=session_id,
    )

    assert response.result["message_count"] == 1
    session = await service.sessions.get(session_id, ctx, auto_create=False)
    await session.load()
    assert session.messages[-1].role_id == "wx/user-01@abc"


async def test_add_message_user_request_autofills_role_id(service, monkeypatch):
    session_id = "user-auto-fill-role-id"
    ctx = RequestContext(
        user=UserIdentifier("acct_session_user", "alice", "assistant-user"),
        role=Role.USER,
    )

    response = await _call_add_message_route(
        service,
        monkeypatch,
        ctx=ctx,
        payload=_message_request("assistant", content="hello user", role_id=None),
        session_id=session_id,
    )

    assert response.result["message_count"] == 1
    session = await service.sessions.get(session_id, ctx, auto_create=False)
    await session.load()
    assert session.messages[-1].role_id == "assistant-user"


async def test_add_message_admin_request_allows_unregistered_user_role_id(service, monkeypatch):
    manager = APIKeyManager(root_key=TEST_ROOT_KEY, viking_fs=service.viking_fs)
    await manager.load()
    account_id = "acct_session_invalid"
    await manager.create_account(account_id, "admin_user")

    ctx = RequestContext(
        user=UserIdentifier(account_id, "admin_user", "assistant-admin"),
        role=Role.ADMIN,
    )

    response = await _call_add_message_route(
        service,
        monkeypatch,
        ctx=ctx,
        payload=_message_request("user", content="hello invalid", role_id="ghost"),
        session_id="invalid-user-role-id",
    )

    assert response.result["message_count"] == 1
    session = await service.sessions.get("invalid-user-role-id", ctx, auto_create=False)
    await session.load()
    assert session.messages[-1].role_id == "ghost"


async def test_add_multiple_messages(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    # Add messages one by one; each add_message call should see
    # the accumulated count (messages are loaded from storage each time)
    resp1 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="Message 0"),
    )
    assert resp1.json()["result"]["message_count"] >= 1

    resp2 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="Message 1"),
    )
    count2 = resp2.json()["result"]["message_count"]

    resp3 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="Message 2"),
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
        json=_message_request("user", content="Message A"),
    )
    assert resp1.status_code == 200
    assert resp1.json()["result"]["message_count"] == 1

    resp2 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="Message B"),
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


async def test_get_session_pending_tokens_counts_tool_only_messages(
    client: httpx.AsyncClient, service
):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]
    tool_output = "x" * 120

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request(
            "user",
            parts=[
                {
                    "type": "tool",
                    "tool_id": "call-1",
                    "tool_name": "shell",
                    "tool_output": tool_output,
                    "tool_status": "completed",
                }
            ],
        ),
    )
    assert resp.status_code == 200

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    expected_tokens = session.messages[0].estimated_tokens
    assert expected_tokens > 0
    assert session.messages[0].content == ""

    get_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["result"]["pending_tokens"] == expected_tokens


async def test_delete_session(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    # Add a message so the session file exists in storage
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="ensure persisted"),
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
        json=_message_request("user", content="Hello"),
    )

    # Default wait=False: returns accepted with task_id
    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "accepted"
    assert "usage" not in body
    assert "telemetry" not in body


async def test_commit_updates_archive_metadata_before_background_task(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    for content in ["first", "second", "third"]:
        resp = await client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json=_message_request("user", content=content),
        )
        assert resp.status_code == 200

    before_commit = await client.get(f"/api/v1/sessions/{session_id}")
    assert before_commit.status_code == 200
    before_result = before_commit.json()["result"]
    assert before_result["message_count"] == 3
    assert before_result["total_message_count"] == 3
    assert before_result["commit_count"] == 0
    assert before_result["last_commit_at"] == ""

    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert commit_resp.status_code == 200
    commit_result = commit_resp.json()["result"]
    assert commit_result["archived"] is True

    immediate_get = await client.get(f"/api/v1/sessions/{session_id}")
    assert immediate_get.status_code == 200
    immediate_result = immediate_get.json()["result"]
    assert immediate_result["message_count"] == 0
    assert immediate_result["total_message_count"] == 3
    assert immediate_result["commit_count"] == 1
    assert immediate_result["last_commit_at"] != ""

    await _wait_for_task(client, commit_result["task_id"])

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="fourth"),
    )
    assert resp.status_code == 200

    after_new_message = await client.get(f"/api/v1/sessions/{session_id}")
    assert after_new_message.status_code == 200
    after_result = after_new_message.json()["result"]
    assert after_result["message_count"] == 1
    assert after_result["total_message_count"] == 4
    assert after_result["commit_count"] == 1


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
        json=_message_request("user", content="archived message"),
    )
    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    task_id = commit_resp.json()["result"]["task_id"]
    await _wait_for_task(client, task_id)

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request(
            "assistant",
            parts=[
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
        ),
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
        json=_message_request("user", content="archived question"),
    )
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("assistant", content="archived answer"),
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
        json=_message_request("user", content="first round"),
    )
    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    task_id = commit_resp.json()["result"]["task_id"]
    task = await _wait_for_task(client, task_id)
    assert task["status"] == "failed"

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="second round"),
    )
    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")

    assert resp.status_code == 412
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "FAILED_PRECONDITION"
    assert "unresolved failed archive" in body["error"]["message"]
