# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for bot proxy endpoint auth enforcement."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import openviking.server.routers.bot as bot_router_module
import openviking.server.routers.compile as compile_router_module
import openviking.service.compile_service as compile_service_module
import openviking.service.external_task_service as external_task_service_module
from openviking.server.auth.plugins import DevAuthPlugin, TrustedAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.identity import AuthMode
from openviking.service.compile_service import CompileService
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_tracker import TaskRecord, TaskStatus
from openviking_cli.utils.config.open_viking_config import CompileApiConfig


def test_set_bot_api_key_updates_module_state():
    bot_router_module.set_bot_api_key("gateway-secret")
    assert bot_router_module.BOT_API_KEY == "gateway-secret"

    bot_router_module.set_bot_api_key("")
    assert bot_router_module.BOT_API_KEY == ""


async def test_create_bot_proxy_client_disables_env_proxy():
    async with bot_router_module._create_bot_proxy_client() as client:
        assert isinstance(client, httpx.AsyncClient)
        assert client._trust_env is False


@pytest.mark.asyncio
async def test_feedback_proxy_forwards_request(monkeypatch):
    forwarded = {}

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.text = '{"accepted": true}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"accepted": True, "response_id": "resp-123"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers, timeout):
            forwarded["url"] = url
            forwarded["json"] = json
            forwarded["headers"] = headers
            forwarded["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(bot_router_module, "BOT_API_URL", "http://127.0.0.1:18790")
    monkeypatch.setattr(bot_router_module, "BOT_API_KEY", "gateway-secret")
    monkeypatch.setattr(bot_router_module, "_create_bot_proxy_client", lambda: FakeClient())

    app = FastAPI()
    app.state.config = SimpleNamespace(get_effective_auth_mode=lambda: AuthMode.DEV)
    app.state.auth_plugin = DevAuthPlugin()
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "session-1",
                "response_id": "resp-123",
                "feedback_type": "thumb_up",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "response_id": "resp-123"}
    assert forwarded["url"] == "http://127.0.0.1:18790/bot/v1/feedback"
    assert forwarded["json"]["response_id"] == "resp-123"
    assert forwarded["headers"]["X-Gateway-Token"] == "gateway-secret"
    assert forwarded["timeout"] == 30.0


@pytest.mark.asyncio
async def test_chat_proxy_attaches_authenticated_openviking_connection(monkeypatch):
    forwarded = {}

    class FakeResponse:
        status_code = 200
        text = '{"session_id": "session-1", "message": "ok"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"session_id": "session-1", "message": "ok"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers, timeout):
            forwarded["url"] = url
            forwarded["json"] = json
            forwarded["headers"] = headers
            forwarded["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(bot_router_module, "BOT_API_URL", "http://127.0.0.1:18790")
    monkeypatch.setattr(bot_router_module, "BOT_API_KEY", "gateway-secret")
    monkeypatch.setattr(bot_router_module, "_create_bot_proxy_client", lambda: FakeClient())

    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="trusted", host="127.0.0.1", port=1944)
    app.state.auth_plugin = TrustedAuthPlugin()
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/bot/v1/chat",
            headers={
                "X-API-Key": "active-user-key",
                "X-OpenViking-Account": "acct",
                "X-OpenViking-User": "alice",
            },
            json={"message": "hello", "user_id": "ignored-by-proxy-identity"},
        )

    assert response.status_code == 200
    assert forwarded["url"] == "http://127.0.0.1:18790/bot/v1/chat"
    assert forwarded["json"]["openviking_connection"] == {
        "api_key": "active-user-key",
        "account_id": "acct",
        "user_id": "alice",
        "agent_id": "web-playground",
        "role": "user",
        "api_key_type": "root",
        "server_url": "http://127.0.0.1:1944",
        "namespace_policy": {
            "isolate_user_scope_by_agent": False,
            "isolate_agent_scope_by_user": False,
        },
    }
    assert forwarded["headers"]["X-Gateway-Token"] == "gateway-secret"
    assert forwarded["timeout"] == 300.0


@pytest.mark.asyncio
async def test_chat_proxy_forwards_trusted_request_without_root_api_key(monkeypatch):
    forwarded = {}

    class FakeResponse:
        status_code = 200
        text = '{"session_id": "session-1", "message": "ok"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"session_id": "session-1", "message": "ok"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers, timeout):
            forwarded["url"] = url
            forwarded["json"] = json
            forwarded["headers"] = headers
            forwarded["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(bot_router_module, "BOT_API_URL", "http://127.0.0.1:18790")
    monkeypatch.setattr(bot_router_module, "BOT_API_KEY", "")
    monkeypatch.setattr(bot_router_module, "_create_bot_proxy_client", lambda: FakeClient())

    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="trusted", host="127.0.0.1", port=1955)
    app.state.auth_plugin = TrustedAuthPlugin()
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/bot/v1/chat",
            headers={
                "X-OpenViking-Account": "acct",
                "X-OpenViking-User": "alice",
            },
            json={"message": "hello"},
        )

    assert response.status_code == 200
    assert forwarded["url"] == "http://127.0.0.1:18790/bot/v1/chat"
    assert "api_key" not in forwarded["json"]["openviking_connection"]
    assert forwarded["json"]["openviking_connection"] == {
        "account_id": "acct",
        "user_id": "alice",
        "agent_id": "web-playground",
        "role": "user",
        "api_key_type": "root",
        "server_url": "http://127.0.0.1:1955",
        "namespace_policy": {
            "isolate_user_scope_by_agent": False,
            "isolate_agent_scope_by_user": False,
        },
    }
    assert "X-Gateway-Token" not in forwarded["headers"]
    assert forwarded["timeout"] == 300.0


@pytest.mark.asyncio
async def test_compile_route_uses_ov_owned_task_and_rejects_legacy_routes(monkeypatch):
    calls = {}

    class FakeCompileService:
        async def create(self, body, *, connection, ctx):
            calls["request"] = body.model_dump(mode="json", by_alias=True)
            calls["connection"] = connection
            calls["owner"] = (ctx.account_id, ctx.user.user_id)
            return TaskRecord(
                task_id="cmp_1",
                task_type="compile",
                status=TaskStatus.PENDING,
                stage="queued",
                resource_id="viking://resources/source",
                account_id="acct",
                user_id="alice",
                meta={"request": {"to": "viking://resources/wiki"}},
            )

    service = SimpleNamespace(compile=FakeCompileService())
    monkeypatch.setattr(compile_router_module, "get_service", lambda: service)

    app = FastAPI()
    app.state.config = ServerConfig(auth_mode="trusted", host="127.0.0.1", port=1944)
    app.state.auth_plugin = TrustedAuthPlugin()
    app.include_router(compile_router_module.router)
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)
    headers = {
        "X-API-Key": "active-user-key",
        "X-OpenViking-Account": "acct",
        "X-OpenViking-User": "alice",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/api/v1/compile",
            headers=headers,
            json={
                "from": ["viking://resources/source"],
                "to": "viking://resources/wiki",
                "skill": "viking://agent/skills/wiki",
            },
        )
        legacy_created = await client.post("/bot/v1/compile", headers=headers, json={})
        legacy_status = await client.get("/bot/v1/compile/cmp_1", headers=headers)
        legacy_cancel = await client.post(
            "/bot/v1/compile/cmp_1/cancel",
            headers=headers,
        )

    assert created.status_code == 202
    assert created.json()["result"]["task_id"] == "cmp_1"
    assert legacy_created.status_code == 400
    assert "POST /api/v1/compile" in legacy_created.json()["detail"]
    assert "GET /api/v1/tasks/{task_id}" in legacy_created.json()["detail"]
    assert legacy_status.status_code == 400
    assert "GET /api/v1/tasks/{task_id}" in legacy_status.json()["detail"]
    assert legacy_cancel.status_code == 400
    assert "POST /api/v1/tasks/{task_id}/cancel" in legacy_cancel.json()["detail"]
    assert calls["connection"]["api_key"] == "active-user-key"
    assert calls["connection"]["server_url"] == "http://127.0.0.1:1944"
    assert calls["owner"] == ("acct", "alice")


@pytest.mark.asyncio
async def test_compile_api_client_session_protocol_and_runtime_timeout(monkeypatch):
    forwarded = []

    class FakeResponse:
        status_code = 202
        is_success = True

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers, json):
            forwarded.append({"method": method, "url": url, "body": json, "headers": headers})
            if url.endswith("/bot/v1/compile"):
                return FakeResponse({"session_id": "ma-session-1"})
            if url.endswith("/compile/cancel"):
                return FakeResponse(
                    {
                        "status": "cancelled",
                        "stage": "compile: cancelled",
                        "error": None,
                        "meta": {},
                    }
                )
            return FakeResponse(
                {
                    "status": "running",
                    "stage": "compile: running",
                    "error": None,
                    "meta": {"token_usage": {"total_tokens": 12}},
                }
            )

    monkeypatch.setattr(compile_service_module.httpx, "AsyncClient", FakeClient)
    tasks = ExternalTaskService()
    service = CompileService(
        CompileApiConfig(
            base_url="https://compile.example.com",
        ),
        tasks,
        SimpleNamespace(),
    )
    tasks.register(service)
    assert service.runtime_timeout_seconds == 60 * 60
    public_payload, private_payload = service._split_payload(
        compile_service_module.CompileRequest.model_validate(
            {
                "from": ["viking://resources/source"],
                "to": "viking://resources/wiki",
                "skill": "viking://agent/skills/wiki",
                "args": {"model_name": "model-1", "user_key": "model-user-key"},
            }
        )
    )
    assert public_payload["args"] == {"model_name": "model-1"}
    assert private_payload == {"args": {"user_key": "model-user-key"}}
    external_task_id = await service.submit(
        "cmp_ov_1",
        {
            "from": ["viking://resources/source"],
            "to": "viking://resources/wiki",
            "skill": "viking://agent/skills/wiki",
        },
        {
            "args": {"user_key": "model-user-key"},
        },
        {
            "server_url": "https://ov.example.com",
            "api_key": "active-user-key",
            "account_id": "acct",
            "user_id": "alice",
        },
    )
    status_snapshot = await service.get(
        external_task_id,
        {"api_key": "active-user-key"},
    )
    cancel_snapshot = await service.cancel(
        external_task_id,
        {"api_key": "active-user-key"},
    )

    assert external_task_id == "ma-session-1"
    assert [request["url"] for request in forwarded] == [
        "https://compile.example.com/bot/v1/compile",
        "https://compile.example.com/compile/status",
        "https://compile.example.com/compile/cancel",
    ]
    assert all(request["method"] == "POST" for request in forwarded)
    assert "X-Gateway-Token" not in forwarded[0]["headers"]
    assert forwarded[0]["headers"]["Idempotency-Key"] == "cmp_ov_1"
    assert forwarded[0]["headers"]["X-API-Key"] == "active-user-key"
    assert forwarded[0]["body"]["args"]["user_key"] == "model-user-key"
    assert "openviking_connection" not in forwarded[0]["body"]
    assert forwarded[1]["body"] == {"session_id": "ma-session-1"}
    assert status_snapshot.meta == {"token_usage": {"total_tokens": 12}}
    assert cancel_snapshot.status == "cancelled"

    class Tracker:
        def __init__(self):
            self.task = TaskRecord(
                task_id="cmp_ov_1",
                task_type="compile",
                status=TaskStatus.RUNNING,
                stage="polling",
                account_id="acct",
                user_id="alice",
                meta={
                    "request": {
                        "from": ["viking://resources/source"],
                        "to": "viking://resources/wiki",
                        "skill": "viking://agent/skills/wiki",
                    }
                },
            )
            self.auth = {
                "openviking_connection": {"api_key": "active-user-key"},
                "external_request_private": {},
                "external_task_id": "ma-session-1",
                "external_runtime_started_at": (
                    external_task_service_module.time.time()
                    - service.runtime_timeout_seconds
                    - 1
                ),
            }
            self.stage = None
            self.error = None

        async def get(self, *args, **kwargs):
            return self.task

        async def get_task_auth(self, *args, **kwargs):
            return self.auth

        async def start(self, *args, **kwargs):
            return None

        async def update_stage(self, _task_id, stage, **kwargs):
            self.stage = stage

        async def fail(self, _task_id, error, **kwargs):
            self.error = error
            self.task.status = TaskStatus.FAILED

        def is_cancellation_requested(self, _task_id):
            return False

    tracker = Tracker()
    monkeypatch.setattr(external_task_service_module, "get_task_tracker", lambda: tracker)
    forwarded.clear()

    await tasks.execute("cmp_ov_1", "acct", "alice")

    assert [request["url"] for request in forwarded] == [
        "https://compile.example.com/compile/cancel"
    ]
    assert tracker.stage == "timed_out"
    assert tracker.task.status == TaskStatus.FAILED
    assert tracker.error == "DEADLINE_EXCEEDED: External task exceeded its runtime limit."


@pytest.mark.asyncio
async def test_chat_stream_proxy_preserves_sse_event_boundaries(monkeypatch):
    payload = (
        'data: {"event":"reasoning_delta","data":"thinking"}\n\n'
        'data: {"event":"response","data":{"content":"done"}}\n\n'
    )

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

        async def aiter_text(self):
            yield payload[:30]
            yield payload[30:]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(bot_router_module, "BOT_API_URL", "http://127.0.0.1:18790")
    monkeypatch.setattr(bot_router_module, "_create_bot_proxy_client", lambda: FakeClient())

    app = FastAPI()
    app.state.config = SimpleNamespace(get_effective_auth_mode=lambda: AuthMode.DEV)
    app.state.auth_plugin = DevAuthPlugin()
    app.include_router(bot_router_module.router, prefix="/bot/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/bot/v1/chat/stream",
            json={"message": "hello"},
        )

    assert response.status_code == 200
    assert response.text == payload
