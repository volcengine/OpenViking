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
                "instruction": "  Keep supporting evidence.  ",
            },
        )
        assert calls["request"]["instruction"] == "Keep supporting evidence."
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
    assert calls["connection"] == {"api_key": "active-user-key"}
    assert calls["owner"] == ("acct", "alice")


@pytest.mark.asyncio
async def test_compile_api_client_session_protocol_retry_and_cancellation(monkeypatch):
    forwarded = []
    response_status = {
        "cancel": "cancelled",
        "submit_failures": 0,
        "cancel_failures": 0,
        "poll": [],
    }

    class FakeResponse:
        def __init__(self, body, status_code=202):
            self._body = body
            self.status_code = status_code
            self.is_success = status_code < 400

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
            if url.endswith("/runtime/v1/tasks"):
                if response_status["submit_failures"]:
                    response_status["submit_failures"] -= 1
                    return FakeResponse({"detail": "temporarily unavailable"}, status_code=503)
                return FakeResponse({"session_id": "ma-session-1"})
            if url.endswith("/runtime/v1/tasks/cancel"):
                if response_status["cancel_failures"]:
                    response_status["cancel_failures"] -= 1
                    return FakeResponse({"detail": "temporarily unavailable"}, status_code=503)
                status = response_status["cancel"]
                return FakeResponse(
                    {
                        "status": status,
                        "stage": f"compile: {status}",
                        "error": None,
                        "meta": {},
                    }
                )
            status = response_status["poll"].pop(0) if response_status["poll"] else "running"
            return FakeResponse(
                {
                    "status": status,
                    "stage": f"compile: {status}",
                    "error": None,
                    "meta": {"token_usage": {"total_tokens": 12}},
                    "result": {"output": "wiki"} if status == "completed" else None,
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
    public_payload, private_payload = service._split_payload(
        compile_service_module.CompileRequest.model_validate(
            {
                "from": ["viking://resources/source"],
                "to": "viking://resources/wiki",
                "skill": "viking://agent/skills/wiki",
                "args": {"model_name": "model-1", "user_key": "model-user-key"},
                "instruction": "Keep supporting evidence.",
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
            "instruction": "Keep supporting evidence.",
        },
        {
            "args": {"user_key": "model-user-key"},
        },
        {"api_key": "active-user-key"},
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
        "https://compile.example.com/runtime/v1/tasks",
        "https://compile.example.com/runtime/v1/tasks/status",
        "https://compile.example.com/runtime/v1/tasks/cancel",
    ]
    assert all(request["method"] == "POST" for request in forwarded)
    assert "X-Gateway-Token" not in forwarded[0]["headers"]
    assert forwarded[0]["headers"]["Idempotency-Key"] == "cmp_ov_1"
    assert forwarded[0]["headers"]["X-API-Key"] == "active-user-key"
    assert forwarded[0]["body"] == {
        "task_type": "compile",
        "payload": {
            "from": ["viking://resources/source"],
            "to": "viking://resources/wiki",
            "skill": "viking://agent/skills/wiki",
            "instruction": "Keep supporting evidence.",
            "args": {"user_key": "model-user-key"},
        },
    }
    assert forwarded[1]["body"] == {"session_id": "ma-session-1"}
    assert status_snapshot.meta == {"token_usage": {"total_tokens": 12}}
    assert cancel_snapshot.status == "cancelled"

    class Tracker:
        def __init__(self):
            self.task = TaskRecord(
                task_id="cmp_ov_1",
                task_type="compile",
                status=TaskStatus.RUNNING,
                stage="compile: running",
                account_id="acct",
                user_id="alice",
                meta={
                    "request": {
                        "from": ["viking://resources/source"],
                        "to": "viking://resources/wiki",
                        "skill": "viking://agent/skills/wiki",
                    },
                    "token_usage": {"total_tokens": 12},
                },
            )
            self.auth = {
                "openviking_connection": {"api_key": "active-user-key"},
                "external_request_private": {},
            }
            self.stage = None
            self.stage_updates = []
            self.error = None

        async def get(self, *args, **kwargs):
            return self.task

        async def get_task_auth(self, *args, **kwargs):
            return self.auth

        async def start(self, *args, **kwargs):
            return None

        async def update_task_auth(self, _task_id, values, **kwargs):
            self.auth.update(values)

        async def update_stage(self, _task_id, stage, **kwargs):
            self.stage = stage
            self.stage_updates.append(stage)
            self.task.stage = stage

        async def fail(self, _task_id, error, **kwargs):
            self.error = error
            self.task.status = TaskStatus.FAILED

        async def complete(self, _task_id, result, **kwargs):
            self.task.result = result
            self.task.status = TaskStatus.COMPLETED

        async def record_cancelled(self, _task_id, **kwargs):
            self.task.status = TaskStatus.CANCELLED

        def is_cancellation_requested(self, _task_id):
            return False

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(external_task_service_module.asyncio, "sleep", no_sleep)
    tracker = Tracker()
    monkeypatch.setattr(external_task_service_module, "get_task_tracker", lambda: tracker)
    forwarded.clear()
    response_status["submit_failures"] = 1
    response_status["poll"] = ["running", "completed"]

    await tasks.execute("cmp_ov_1", "acct", "alice")

    assert [request["url"] for request in forwarded] == [
        "https://compile.example.com/runtime/v1/tasks",
        "https://compile.example.com/runtime/v1/tasks",
        "https://compile.example.com/runtime/v1/tasks/status",
        "https://compile.example.com/runtime/v1/tasks/status",
    ]
    assert forwarded[0]["headers"]["Idempotency-Key"] == "cmp_ov_1"
    assert forwarded[1]["headers"]["Idempotency-Key"] == "cmp_ov_1"
    assert tracker.stage_updates == ["compile: completed"]
    assert tracker.task.status == TaskStatus.COMPLETED
    assert tracker.task.result == {"output": "wiki"}

    forwarded.clear()
    response_status["cancel"] = "cancelling"
    response_status["cancel_failures"] = 3
    response_status["poll"] = ["cancelling"] * 4 + ["cancelled"]
    tracker.task.status = TaskStatus.CANCELLING
    tracker.task.stage = "compile: running"
    tracker.stage_updates.clear()

    await tasks.cancel_recovered("cmp_ov_1", "acct", "alice")

    assert tracker.task.status == TaskStatus.CANCELLED
    assert response_status["cancel_failures"] == 0
    assert response_status["poll"] == []


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
