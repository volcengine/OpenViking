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

from openviking.message import ImagePart, Message, TextPart
from openviking.server.app import create_app
from openviking.server.config import (
    ServerConfig,
    ToolOutputExternalizationConfig,
)
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import sessions as sessions_router
from openviking.server.routers import task_queues as task_queues_router
from openviking.service.open_task_queue import OpenTaskQueueService, open_task_to_dict
from openviking.session.session import Session
from openviking_cli.exceptions import ConflictError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.memory_config import SessionAutoCommitConfig
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
    peer_id: object = _UNSET,
) -> dict:
    payload = {"role": role}
    if content is not None:
        payload["content"] = content
    if parts is not None:
        payload["parts"] = parts
    if peer_id is not _UNSET and peer_id is not None:
        payload["peer_id"] = peer_id
    return payload


def _compile_open_task_request() -> dict:
    return {
        "from": ["viking://resources/project"],
        "to": "viking://resources/wiki",
        "skill": "viking://user/default/skills/wiki",
        "reason": "Generate wiki pages",
        "runtime_timeout_seconds": 3600,
    }


class _FakeOpenTaskQueue:
    def __init__(self, name: str) -> None:
        self.name = name
        self.pending: list[dict] = []
        self.processing: dict[str, dict] = {}
        self.acked: list[str] = []
        self._next_id = 0

    async def enqueue(self, data):
        self._next_id += 1
        msg_id = f"msg_{self._next_id}"
        message = {"id": msg_id, "data": json.dumps(dict(data))}
        self.pending.append(message)
        return msg_id

    async def dequeue_raw(self):
        if not self.pending:
            return None
        message = self.pending.pop(0)
        self.processing[message["id"]] = message
        return message

    async def ack(self, msg_id, message=None):
        del message
        self.processing.pop(msg_id, None)
        self.acked.append(msg_id)

    def recover_processing(self) -> None:
        messages = list(self.processing.values())
        self.processing.clear()
        self.pending.extend(messages)


def _install_fake_open_task_queue(monkeypatch, service, *, lease_seconds: float = 600.0):
    queues: dict[str, _FakeOpenTaskQueue] = {}

    def queue_factory(name: str) -> _FakeOpenTaskQueue:
        queue = queues.get(name)
        if queue is None:
            queue = _FakeOpenTaskQueue(name)
            queues[name] = queue
        return queue

    queue_service = OpenTaskQueueService(
        service.viking_fs.agfs,
        lease_seconds=lease_seconds,
        queue_factory=queue_factory,
    )
    monkeypatch.setattr(task_queues_router, "_open_task_queue_service", lambda: queue_service)
    return queue_service, queues


@pytest.fixture(autouse=True)
def _configure_test_env(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(tmp_path / "workspace"),
                    "agfs": {"backend": "local"},
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


async def _archive_marker_exists(session, archive_uri: str, name: str) -> bool:
    try:
        await session._viking_fs.read_file(f"{archive_uri}/{name}", ctx=session.ctx)
        return True
    except Exception:
        return False


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
    session_uri = create_resp.json()["result"]["uri"]

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["session_id"] == session_id
    assert body["result"]["uri"] == session_uri


async def test_legacy_session_uri_alias_reads_current_user_session(client: httpx.AsyncClient):
    session_id = "legacy-alias-read"
    await client.post("/api/v1/sessions", json={"session_id": session_id})
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="legacy alias message"),
    )

    resp = await client.get(
        "/api/v1/content/read",
        params={"uri": f"viking://session/{session_id}/messages.jsonl"},
    )

    assert resp.status_code == 200
    assert "legacy alias message" in resp.json()["result"]


@pytest.mark.parametrize("artifact", ["empty", "recall_ledger", "meta"])
async def test_add_message_repairs_session_directory_without_live_messages(
    client: httpx.AsyncClient,
    service,
    artifact: str,
):
    session_id = f"{artifact}-only-session"
    ctx = RequestContext(user=DEFAULT_USER, role=Role.ROOT)
    ledger_uri = f"viking://user/default/sessions/{session_id}/.recall_log.json"
    meta_uri = f"viking://user/default/sessions/{session_id}/.meta.json"
    await service.viking_fs.mkdir(
        f"viking://user/default/sessions/{session_id}",
        exist_ok=True,
        ctx=ctx,
    )
    if artifact == "recall_ledger":
        await service.viking_fs.write_file(
            uri=ledger_uri,
            content=json.dumps({"version": 1, "updated_turn": 0, "entries": {}}),
            ctx=ctx,
        )
    elif artifact == "meta":
        meta = service.sessions.session(ctx, session_id).meta.to_dict()
        meta["last_commit_at"] = "preserve-existing-meta"
        await service.viking_fs.write_file(
            uri=meta_uri,
            content=json.dumps(meta),
            ctx=ctx,
        )

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="first captured message"),
    )

    assert resp.status_code == 200
    assert resp.json()["result"]["message_count"] == 1
    messages = await service.viking_fs.read_file(
        f"viking://user/default/sessions/{session_id}/messages.jsonl",
        ctx=ctx,
    )
    assert "first captured message" in messages
    assert await service.viking_fs.exists(ledger_uri, ctx=ctx) is (artifact == "recall_ledger")
    persisted_meta = json.loads(await service.viking_fs.read_file(meta_uri, ctx=ctx))
    if artifact == "meta":
        assert persisted_meta["last_commit_at"] == "preserve-existing-meta"


async def test_partial_session_remains_visible_and_deletable(
    client: httpx.AsyncClient,
    service,
):
    session_id = "partial-session-lifecycle"
    ctx = RequestContext(user=DEFAULT_USER, role=Role.ROOT)
    session_uri = f"viking://user/default/sessions/{session_id}"
    await service.viking_fs.mkdir(session_uri, exist_ok=True, ctx=ctx)

    get_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["result"]["message_count"] == 0

    duplicate = await client.post("/api/v1/sessions", json={"session_id": session_id})
    assert duplicate.status_code == 409

    delete_resp = await client.delete(f"/api/v1/sessions/{session_id}")
    assert delete_resp.status_code == 200
    assert not await service.viking_fs.exists(session_uri, ctx=ctx)


async def test_add_message_recovers_after_interrupted_session_initialization(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    session_id = "interrupted-initialization"
    ctx = RequestContext(user=DEFAULT_USER, role=Role.ROOT)
    session_uri = f"viking://user/default/sessions/{session_id}"
    original_write_file = service.viking_fs.write_file
    failed_once = False

    async def fail_first_message_file(uri, content, **kwargs):
        nonlocal failed_once
        if uri == f"{session_uri}/messages.jsonl" and not failed_once:
            failed_once = True
            raise OSError("simulated initialization interruption")
        return await original_write_file(uri, content, **kwargs)

    monkeypatch.setattr(service.viking_fs, "write_file", fail_first_message_file)
    first = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="first attempt"),
    )
    assert first.status_code == 500
    assert await service.viking_fs.exists(session_uri, ctx=ctx)
    assert not await service.viking_fs.exists(f"{session_uri}/messages.jsonl", ctx=ctx)

    second = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="second attempt"),
    )
    assert second.status_code == 200
    assert second.json()["result"]["message_count"] == 1
    messages = await service.viking_fs.read_file(f"{session_uri}/messages.jsonl", ctx=ctx)
    assert "second attempt" in messages


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


async def test_tool_result_externalization_read_and_search(client: httpx.AsyncClient):
    session_id = "tool-result-api-session"
    raw = "alpha\n" + ("needle-" * 4000) + "\nomega"

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request(
            "user",
            parts=[
                {
                    "type": "tool",
                    "tool_id": "call_api",
                    "tool_name": "read_file",
                    "tool_output": raw,
                    "tool_status": "completed",
                }
            ],
        ),
    )
    assert resp.status_code == 200

    context_resp = await client.get(f"/api/v1/sessions/{session_id}/context")
    assert context_resp.status_code == 200
    session_resp = await client.get(f"/api/v1/sessions/{session_id}")
    session_uri = session_resp.json()["result"]["uri"]
    part = context_resp.json()["result"]["messages"][0]["parts"][0]
    assert part["tool_output_truncated"] is True
    assert part["tool_output_ref"].startswith(f"{session_uri}/tool-results/")
    assert raw not in part["tool_output"]
    assert "kind: text" in part["tool_output"]
    assert "openviking_tool_result_search" in part["tool_output"]

    tool_result_id = part["tool_output_ref"].rsplit("/", 1)[-1]
    read_resp = await client.get(
        f"/api/v1/sessions/{session_id}/tool-results/{tool_result_id}?offset=0&limit=-1"
    )
    assert read_resp.status_code == 200
    read_body = read_resp.json()["result"]
    assert read_body["content"] == raw
    assert read_body["offset_unit"] == "unicode_code_point"
    assert read_body["metadata"]["synopsis_kind"] == "text"
    assert read_body["metadata"]["synopsis"]["kind"] == "text"

    list_resp = await client.get(f"/api/v1/sessions/{session_id}/tool-results")
    assert list_resp.status_code == 200
    listed = list_resp.json()["result"]["tool_results"]
    assert len(listed) == 1
    assert listed[0]["tool_result_id"] == tool_result_id
    assert listed[0]["synopsis_kind"] == "text"
    assert listed[0]["synopsis"]["kind"] == "text"

    search_resp = await client.get(
        f"/api/v1/sessions/{session_id}/tool-results/{tool_result_id}/search",
        params={"q": "needle", "limit": 1, "context_chars": 5},
    )
    assert search_resp.status_code == 200
    matches = search_resp.json()["result"]["matches"]
    assert len(matches) == 1
    assert matches[0]["offset_unit"] == "unicode_code_point"
    assert "needle" in matches[0]["snippet"]


async def test_tool_result_externalization_respects_server_config_disabled(service):
    from openviking.server.auth.plugins import DevAuthPlugin

    app = create_app(
        config=ServerConfig(
            tool_output_externalization=ToolOutputExternalizationConfig(enabled=False)
        ),
        service=service,
    )
    set_service(service)
    # ASGITransport does not run the application lifespan that normally
    # initializes the configured auth plugin.
    app.state.auth_plugin = DevAuthPlugin()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_id = "tool-result-disabled-session"
        raw = "disabled-" * 4000

        resp = await client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json=_message_request(
                "user",
                parts=[
                    {
                        "type": "tool",
                        "tool_id": "call_disabled",
                        "tool_name": "read_file",
                        "tool_output": raw,
                        "tool_status": "completed",
                    }
                ],
            ),
        )
        assert resp.status_code == 200

        context_resp = await client.get(f"/api/v1/sessions/{session_id}/context")
        assert context_resp.status_code == 200
        part = context_resp.json()["result"]["messages"][0]["parts"][0]
        assert part["tool_output"] == raw
        assert "tool_output_ref" not in part
        assert "tool_output_truncated" not in part


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
        Message(
            id="pending-user",
            role="user",
            parts=[TextPart("Pending user message")],
            peer_id=DEFAULT_USER.user_id,
        ),
        Message(
            id="pending-assistant",
            role="assistant",
            parts=[TextPart("Pending assistant response")],
            peer_id="assistant-default",
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
        "Archived seed",
        "Pending user message",
        "Pending assistant response",
        "Current live message",
    ]


async def test_get_session_context_stops_at_newest_failed_archive(
    client: httpx.AsyncClient, service
):
    """The read path stops at the newest terminal archive.

    Archive history grows without bound, so ``get_session_context`` no longer
    walks it. A newest ``.failed.json`` therefore contributes no overview and no
    replayed raw messages; the raw file stays durable and Phase 2 roll-forward
    still absorbs it. This is a deliberate deviation from the RFC #3330
    ``logical live`` formula.
    """
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
    failed_messages = [
        Message(
            id="failed-user",
            role="user",
            parts=[TextPart("Failed archive message")],
            peer_id=DEFAULT_USER.user_id,
        )
    ]
    failed_archive_uri = f"{session.uri}/history/archive_002"
    await session._viking_fs.write_file(
        uri=f"{failed_archive_uri}/messages.jsonl",
        content="\n".join(msg.to_jsonl() for msg in failed_messages) + "\n",
        ctx=session.ctx,
    )
    await session._viking_fs.write_file(
        uri=f"{failed_archive_uri}/.failed.json",
        content=json.dumps({"stage": "memory_extraction", "error": "synthetic"}),
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
        "Current live message",
    ]
    assert body["result"]["stats"]["failedArchives"] == 1

    # The failed archive's raw messages are still durable on disk.
    raw = await session._read_archive_messages(failed_archive_uri)
    assert [message.content for message in raw] == ["Failed archive message"]


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


async def test_add_message_records_last_message_at(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Hello, world!"},
    )
    assert resp.status_code == 200

    session_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert session_resp.status_code == 200
    assert session_resp.json()["result"]["last_message_at"]


async def test_direct_session_add_message_records_last_message_at(service):
    session = await service.sessions.get(
        "direct-last-message-at-session",
        RequestContext(user=DEFAULT_USER, role=Role.ROOT),
        auto_create=True,
    )
    assert session.meta.last_message_at == ""

    session.add_message("user", [TextPart("Hello, world!")])

    assert session.meta.last_message_at


async def test_add_message_records_last_message_at_with_single_meta_save(
    client: httpx.AsyncClient,
    monkeypatch,
):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]
    save_session_ids = []
    original_save_meta = Session._save_meta

    async def counting_save_meta(self, *args, **kwargs):
        save_session_ids.append(self.session_id)
        await original_save_meta(self, *args, **kwargs)

    monkeypatch.setattr(Session, "_save_meta", counting_save_meta)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Hello, world!"},
    )
    assert resp.status_code == 200

    assert save_session_ids == [session_id]

    session_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert session_resp.status_code == 200
    assert session_resp.json()["result"]["last_message_at"]


async def test_add_message_ignores_extra_metadata_fields(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "Hello, world!",
            "metadata": {"source": "test"},
            "auto_commit_policy": {"pending_token_threshold": 123},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["message_count"] == 1


async def test_create_session_defaults_auto_commit_policy_to_disabled(
    client: httpx.AsyncClient,
):
    resp = await client.post("/api/v1/sessions", json={})
    assert resp.status_code == 200
    assert resp.json()["result"]["auto_commit_policy"] is None


async def test_create_session_uses_default_policy_when_server_default_enabled(
    client: httpx.AsyncClient,
    service,
):
    service.sessions.set_session_auto_commit_config(SessionAutoCommitConfig(default_enabled=True))

    resp = await client.post("/api/v1/sessions", json={})
    assert resp.status_code == 200
    assert resp.json()["result"]["auto_commit_policy"] == {
        "pending_token_threshold": 10000,
        "message_count_threshold": 50,
        "idle_timeout_seconds": 86400,
        "keep_recent_count": 2,
        "min_commit_interval_seconds": 0,
    }


async def test_create_session_can_disable_server_default_auto_commit(
    client: httpx.AsyncClient,
    service,
):
    service.sessions.set_session_auto_commit_config(SessionAutoCommitConfig(default_enabled=True))

    resp = await client.post(
        "/api/v1/sessions",
        json={"auto_commit_policy": None},
    )

    assert resp.status_code == 200
    assert resp.json()["result"]["auto_commit_policy"] is None


async def test_auto_commit_policy_rejects_null_fields(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/sessions",
        json={"auto_commit_policy": {"message_count_threshold": None}},
    )

    assert resp.status_code == 400
    assert "auto_commit_policy=null" in resp.json()["error"]["message"]


async def test_auto_created_session_uses_default_policy_when_server_default_enabled(
    client: httpx.AsyncClient,
    service,
):
    service.sessions.set_session_auto_commit_config(SessionAutoCommitConfig(default_enabled=True))
    session_id = "auto-created-default-policy"

    add_resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "create this session by writing first"},
    )
    assert add_resp.status_code == 200

    session_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert session_resp.status_code == 200
    assert session_resp.json()["result"]["auto_commit_policy"] == {
        "pending_token_threshold": 10000,
        "message_count_threshold": 50,
        "idle_timeout_seconds": 86400,
        "keep_recent_count": 2,
        "min_commit_interval_seconds": 0,
    }


async def test_create_session_applies_config_and_fills_defaults(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/sessions",
        json={
            "auto_commit_policy": {
                "pending_token_threshold": 8000,
                "keep_recent_count": 10,
            }
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["auto_commit_policy"] == {
        "pending_token_threshold": 8000,
        "message_count_threshold": 50,
        "idle_timeout_seconds": 86400,
        "keep_recent_count": 10,
        "min_commit_interval_seconds": 0,
    }

    session_resp = await client.get(f"/api/v1/sessions/{result['session_id']}")
    session_result = session_resp.json()["result"]
    assert session_result["auto_commit_policy"]["pending_token_threshold"] == 8000
    assert session_result["auto_commit_policy"]["keep_recent_count"] == 10


async def test_create_session_clamps_config_above_bounds(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/sessions",
        json={"auto_commit_policy": {"pending_token_threshold": 10_000_000}},
    )
    assert resp.status_code == 200
    policy = resp.json()["result"]["auto_commit_policy"]
    assert policy["pending_token_threshold"] == 50000


async def test_get_session_returns_effective_config(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["result"]["auto_commit_policy"] is None


async def test_patch_session_config_is_not_supported(client: httpx.AsyncClient):
    create_resp = await client.post(
        "/api/v1/sessions",
        json={
            "auto_commit_policy": {
                "pending_token_threshold": 8000,
                "message_count_threshold": 40,
                "keep_recent_count": 10,
            }
        },
    )
    session_id = create_resp.json()["result"]["session_id"]

    patch_resp = await client.patch(
        f"/api/v1/sessions/{session_id}",
        json={"auto_commit_policy": {"message_count_threshold": 25}},
    )
    assert patch_resp.status_code == 405

    session_resp = await client.get(f"/api/v1/sessions/{session_id}")
    session_config = session_resp.json()["result"]["auto_commit_policy"]
    assert session_config["message_count_threshold"] == 40
    assert session_config["pending_token_threshold"] == 8000


async def test_session_load_recovers_message_count_from_live_messages(service):
    ctx = RequestContext(user=UserIdentifier("acct_a", "user_b"), role=Role.ADMIN)
    await service.initialize_account_directories(ctx)
    await service.initialize_user_directories(ctx)

    session = await service.sessions.create(ctx)
    session.add_message("user", [TextPart("我爱吃西瓜")])

    session = await service.sessions.get(session.session_id, ctx, auto_create=False)
    session.meta.message_count = 0
    session.meta.pending_tokens = 0
    await session._save_meta()

    reloaded = service.sessions.session(ctx, session.session_id)
    await reloaded.load()

    assert len(reloaded.messages) == 1
    assert reloaded.meta.message_count == 1


async def test_write_responses_return_pending_tokens_for_commit_policy(
    client: httpx.AsyncClient,
):
    """A commit policy must be able to decide from the write response alone.

    Exercises the real REST endpoints rather than a test double, so the
    ``pending_tokens`` contract is verified where LangChain actually reads it.
    """
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    add_resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="Hello, world!"),
    )
    assert add_resp.status_code == 200
    after_add = add_resp.json()["result"]["pending_tokens"]
    assert after_add > 0

    batch_resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages/batch",
        json={
            "messages": [
                _message_request("assistant", content="First reply"),
                _message_request("user", content="Follow-up question"),
            ]
        },
    )
    assert batch_resp.status_code == 200
    after_batch = batch_resp.json()["result"]["pending_tokens"]
    assert after_batch > after_add

    # The write-returned value must match what get_session would have reported,
    # which is exactly the extra round trip this field removes.
    get_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert get_resp.json()["result"]["pending_tokens"] == after_batch


async def test_add_message_accepts_image_part(client: httpx.AsyncClient, service):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request(
            "user",
            parts=[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://example.com/image.png",
                        "detail": "auto",
                    },
                }
            ],
        ),
    )

    assert resp.status_code == 200
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    assert isinstance(session.messages[0].parts[0], ImagePart)
    assert session.messages[0].parts[0].url == "https://example.com/image.png"
    assert session.messages[0].parts[0].detail == "auto"

    context_resp = await client.get(f"/api/v1/sessions/{session_id}/context")
    assert context_resp.status_code == 200
    assert context_resp.json()["result"]["messages"][0]["parts"][0] == {
        "type": "image_url",
        "image_url": {
            "url": "https://example.com/image.png",
            "detail": "auto",
        },
    }


async def test_add_message_resolves_image_part_url_path_variables(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    monkeypatch.setattr(
        "openviking.server.routers.sessions.resolve_path_variables",
        lambda value: value.replace("{calendar:today}", "2026/06/15"),
    )
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request(
            "user",
            parts=[
                {
                    "type": "image_url",
                    "image_url": {"url": "viking://resources/images/{calendar:today}/photo.png"},
                }
            ],
        ),
    )

    assert resp.status_code == 200
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    assert isinstance(session.messages[0].parts[0], ImagePart)
    assert session.messages[0].parts[0].url == "viking://resources/images/2026/06/15/photo.png"


async def test_add_message_accepts_mixed_parts(client: httpx.AsyncClient, service):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request(
            "user",
            parts=[
                {"type": "text", "text": "Look at this"},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://example.com/image.png"},
                },
            ],
        ),
    )

    assert resp.status_code == 200
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    assert isinstance(session.messages[0].parts[0], TextPart)
    assert session.messages[0].parts[0].text == "Look at this"
    assert isinstance(session.messages[0].parts[1], ImagePart)
    assert session.messages[0].parts[1].url == "https://example.com/image.png"


async def test_add_message_rejects_image_part_without_url(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request(
            "user",
            parts=[{"type": "image_url", "image_url": {}}],
        ),
    )

    assert resp.status_code == 400
    assert "image_url part requires a non-empty URL" in resp.text


async def test_add_message_rejects_openai_style_image_content(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": {
                "type": "image_url",
            },
        },
    )

    assert resp.status_code == 400


async def test_batch_add_message_accepts_mixed_parts(client: httpx.AsyncClient, service):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages/batch",
        json={
            "messages": [
                _message_request(
                    "user",
                    parts=[
                        {"type": "text", "text": "Look at this"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/image.png"},
                        },
                    ],
                )
            ]
        },
    )

    assert resp.status_code == 200
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    assert isinstance(session.messages[0].parts[0], TextPart)
    assert isinstance(session.messages[0].parts[1], ImagePart)


async def test_batch_add_message_ignores_removed_auto_commit_policy(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages/batch",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "auto_commit_policy": {"pending_token_threshold": 1},
        },
    )

    assert resp.status_code == 200
    assert resp.json()["result"]["message_count"] == 1


async def test_add_message_splits_tool_result_aggregate(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request(
            "user",
            parts=[
                {
                    "type": "tool",
                    "tool_id": "call_a",
                    "tool_name": "tool_a",
                    "tool_output": "a",
                    "tool_status": "completed",
                },
                {
                    "type": "tool",
                    "tool_id": "call_b",
                    "tool_name": "tool_b",
                    "tool_output": "b",
                    "tool_status": "completed",
                },
            ],
        ),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["message_count"] == 2


async def test_add_message_request_persists_peer_id(service, monkeypatch):
    session_id = "trusted-peer-id"
    ctx = RequestContext(
        user=UserIdentifier("acct_trusted", "caller"),
        role=Role.USER,
    )

    response = await _call_add_message_route(
        service,
        monkeypatch,
        ctx=ctx,
        payload=_message_request("assistant", content="hello trusted", peer_id="assistant-b"),
        session_id=session_id,
    )

    assert response.result["message_count"] == 1
    session = await service.sessions.get(session_id, ctx, auto_create=False)
    await session.load()
    assert session.messages[-1].peer_id == "assistant-b"


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
    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    await _wait_for_task(client, commit_resp.json()["result"]["task_id"])

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
    assert "memory_diff_uri" not in body["result"]
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
        return [FakeMemory("viking://user/default/memories/mock.md")]

    monkeypatch.setattr(service.sessions, "extract", fake_extract)

    create_resp = await client.post("/api/v1/sessions", json={"user": "test"})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(f"/api/v1/sessions/{session_id}/extract")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == [{"uri": "viking://user/default/memories/mock.md"}]


async def test_get_session_context_endpoint_returns_trimmed_latest_archive_and_messages(
    client: httpx.AsyncClient,
    service,
):
    # Memory extraction (long-term/execution) uses a VLM backend the server fake
    # does not cover; stub it so the archive completes deterministically. This
    # test exercises the context endpoint, not memory extraction.
    async def _no_memories(*args, **kwargs):
        del args, kwargs
        return []

    service.sessions._session_compressor.extract_long_term_memories = _no_memories

    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]
    session_uri = create_resp.json()["result"]["uri"]

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
                    "tool_uri": f"{session_uri}/tools/tool_123",
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
    assert result["messages"] == []
    assert result["estimatedTokens"] <= 1
    assert result["stats"]["activeTokens"] <= 1
    assert result["stats"]["totalArchives"] == 1
    assert result["stats"]["includedArchives"] == 0
    assert result["stats"]["droppedArchives"] == 1
    assert result["stats"]["failedArchives"] == 0

    # Budget fitting is a virtual view and must not mutate the durable live row.
    full_resp = await client.get(f"/api/v1/sessions/{session_id}/context?token_budget=128000")
    full_result = full_resp.json()["result"]
    assert len(full_result["messages"]) == 1
    assert full_result["messages"][0]["role"] == "assistant"
    assert any(
        part["type"] == "tool" and part["tool_id"] == "tool_123"
        for part in full_result["messages"][0]["parts"]
    )


async def test_get_session_archive_endpoint_returns_archive_details(
    client: httpx.AsyncClient,
    service,
):
    # See test_get_session_context_*: stub memory extraction (not covered by the
    # server fake VLM) so the archive completes; this test checks the archive
    # endpoint, not memory extraction.
    async def _no_memories(*args, **kwargs):
        del args, kwargs
        return []

    service.sessions._session_compressor.extract_long_term_memories = _no_memories

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


async def test_commit_failed_when_long_term_extraction_fails_does_not_block_next_commit(
    client: httpx.AsyncClient,
    service,
):
    """Binary archive outcome: if long-term memory extraction fails (after
    retries), the whole archive is marked .failed.json and skipped — there is
    no partial state — but a failed archive must not block the next commit.
    """
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
    # Any Phase 2 step failing fails the whole archive (no partial state).
    assert task["status"] == "failed"

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    archive_uri = f"{session.uri}/history/archive_001"
    assert await _archive_marker_exists(session, archive_uri, ".failed.json")
    assert not await _archive_marker_exists(session, archive_uri, ".done")
    assert not await _archive_marker_exists(session, archive_uri, ".partial.json")

    # The failed archive is skipped, not retrievable as a completed archive.
    archive_resp = await client.get(f"/api/v1/sessions/{session_id}/archives/archive_001")
    archive_body = archive_resp.json()
    assert archive_body["status"] == "error"
    assert archive_body["error"]["code"] == "NOT_FOUND"

    # A failed archive is a skippable terminal state; the next commit proceeds.
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="second round"),
    )
    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["archived"] is True


async def test_commit_failed_when_summary_fails_does_not_block_next_commit(
    client: httpx.AsyncClient,
    service,
):
    """If the core Working Memory summary fails, the archive is .failed.json (no
    .done) and the task fails — but a failed archive must not block later commits.
    """
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    async def failing_summary(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("synthetic summary failure")

    with patch(
        "openviking.session.session.Session._generate_archive_summary_async",
        new=failing_summary,
    ):
        await client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json=_message_request("user", content="first round"),
        )
        commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
        task_id = commit_resp.json()["result"]["task_id"]
        task = await _wait_for_task(client, task_id)
        assert task["status"] == "failed"

        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session = service.sessions.session(ctx, session_id)
        await session.load()
        archive_uri = f"{session.uri}/history/archive_001"
        assert await _archive_marker_exists(session, archive_uri, ".failed.json")
        assert not await _archive_marker_exists(session, archive_uri, ".done")

    # A failed archive is a skippable terminal state; the next commit proceeds.
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json=_message_request("user", content="second round"),
    )
    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["archived"] is True


async def test_compile_open_task_queue_claim_complete_ack_with_worker_identity(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    _queue_service, queues = _install_fake_open_task_queue(monkeypatch, service)
    owner_headers = {"X-OpenViking-Account": "queue-acct", "X-OpenViking-User": "alice"}
    worker_headers = {"X-OpenViking-Account": "worker-acct", "X-OpenViking-User": "bob"}
    task_owner = {"account_id": "queue-acct", "user_id": "alice"}

    create_resp = await client.post(
        "/api/v1/task-queues/compile/tasks",
        json=_compile_open_task_request(),
        headers=owner_headers,
    )

    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()["result"]
    task_id = created["task_id"]
    assert task_id
    assert created["task_type"] == "compile"
    assert created["status"] == "pending"
    assert created["created_by_user_id"] == "alice"
    assert created["user_id"] == "alice"
    assert created["payload"]["from"] == ["viking://resources/project"]
    assert "lease_id" not in created
    assert "queue_name" not in created
    assert "queue_message_id" not in created
    assert service.viking_fs.agfs.exists(f"/local/queue-acct/_system/tasks/alice/{task_id}.json")
    assert not service.viking_fs.agfs.exists(
        f"/local/queue-acct/_system/open_task_queue/compile/{task_id}/task.json"
    )
    queue = queues["OpenCompileTask"]
    assert len(queue.pending) == 1
    assert queue.processing == {}

    forbidden_connection = await client.post(
        "/api/v1/task-queues/compile/tasks",
        json={
            **_compile_open_task_request(),
            "openviking_connection": {"api_key": "secret"},
        },
        headers=owner_headers,
    )
    assert forbidden_connection.status_code == 400

    claim_resp = await client.post(
        "/api/v1/task-queues/compile/claim",
        headers=worker_headers,
    )
    assert claim_resp.status_code == 200, claim_resp.text
    claimed = claim_resp.json()["result"]
    lease_id = claimed["lease_id"]
    assert claimed["task_id"] == task_id
    assert claimed["status"] == "running"
    assert claimed["attempt"] == 1
    assert claimed["account_id"] == "queue-acct"
    assert claimed["user_id"] == "alice"
    assert claimed["claimed_by_account_id"] == "worker-acct"
    assert claimed["claimed_by_user_id"] == "bob"
    assert queue.pending == []
    assert set(queue.processing) == {"msg_1"}

    no_more_work = await client.post(
        "/api/v1/task-queues/compile/claim",
        headers=owner_headers,
    )
    assert no_more_work.status_code == 200
    assert no_more_work.json()["result"] is None

    update_resp = await client.patch(
        f"/api/v1/task-queues/compile/tasks/{task_id}",
        json={
            **task_owner,
            "lease_id": lease_id,
            "stage": "generating",
            "progress": 0.4,
            "message": "Generating pages",
            "details": {"pages": 3},
        },
        headers=worker_headers,
    )
    assert update_resp.status_code == 200, update_resp.text
    updated = update_resp.json()["result"]
    assert updated["stage"] == "generating"
    assert updated["progress"] == 0.4
    assert updated["details"] == {"pages": 3}

    wrong_lease = await client.patch(
        f"/api/v1/task-queues/compile/tasks/{task_id}",
        json={**task_owner, "lease_id": "lease_wrong", "progress": 0.6},
        headers=worker_headers,
    )
    assert wrong_lease.status_code == 409

    complete_resp = await client.post(
        f"/api/v1/task-queues/compile/tasks/{task_id}/complete",
        json={
            **task_owner,
            "lease_id": lease_id,
            "result": {
                "from": ["viking://resources/project"],
                "to": "viking://resources/wiki",
                "skill": "viking://user/default/skills/wiki",
                "created": ["viking://resources/wiki/intro.md"],
                "updated": [],
                "unchanged": [],
                "page_count": 1,
                "link_count": 0,
                "warnings": [],
            },
        },
        headers=worker_headers,
    )
    assert complete_resp.status_code == 200, complete_resp.text
    completed = complete_resp.json()["result"]
    assert completed["status"] == "completed"
    assert completed["stage"] == "completed"
    assert completed["progress"] == 1.0
    assert completed["result"]["created"] == ["viking://resources/wiki/intro.md"]
    assert set(queue.processing) == {"msg_1"}
    assert queue.acked == []

    ack_resp = await client.post(
        f"/api/v1/task-queues/compile/tasks/{task_id}/ack",
        json={**task_owner, "lease_id": lease_id},
        headers=worker_headers,
    )
    assert ack_resp.status_code == 200, ack_resp.text
    acked = ack_resp.json()["result"]
    assert acked["ack_by_account_id"] == "worker-acct"
    assert acked["ack_by"] == "bob"
    assert acked["acknowledged_at"] is not None
    assert queue.processing == {}
    assert queue.acked == ["msg_1"]


async def test_compile_open_task_queue_recovered_expired_lease_can_be_claimed_again(
    service,
    monkeypatch,
):
    queue_service, queues = _install_fake_open_task_queue(
        monkeypatch,
        service,
        lease_seconds=-1.0,
    )
    created = await queue_service.create_compile_task(
        account_id="lease-acct",
        user_id="alice",
        payload=_compile_open_task_request(),
    )
    queue = queues["OpenCompileTask"]
    first_claim = await queue_service.claim_compile_task(
        worker_account_id="lease-acct",
        worker_user_id="alice",
    )

    assert first_claim is not None
    first_claimed = open_task_to_dict(first_claim, include_lease_id=True)
    assert first_claimed["lease_id"] is not None
    with pytest.raises(ConflictError):
        await queue_service.update_task(
            account_id="lease-acct",
            user_id="alice",
            task_id=created.task_id,
            lease_id=first_claimed["lease_id"],
            updates={"progress": 0.2},
        )

    queue.recover_processing()
    second_claim = await queue_service.claim_compile_task(
        worker_account_id="worker-acct",
        worker_user_id="bob",
    )

    assert second_claim is not None
    second_claimed = open_task_to_dict(second_claim, include_lease_id=True)
    assert second_claim.task_id == created.task_id
    assert second_claimed["attempt"] == 2
    assert second_claimed["claimed_by_account_id"] == "worker-acct"
    assert second_claimed["claimed_by_user_id"] == "bob"
    assert second_claimed["lease_id"] != first_claimed["lease_id"]
