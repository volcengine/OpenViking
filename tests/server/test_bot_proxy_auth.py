# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for bot proxy endpoint auth enforcement."""

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

import openviking.server.routers.bot as bot_router_module
import openviking.server.routers.compile as compile_router_module
import openviking.service.compile_service as compile_service_module
import openviking.service.external_task_service as external_task_service_module
from openviking.server.auth.plugins import DevAuthPlugin, TrustedAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.identity import AuthMode, RequestContext, Role
from openviking.service.compile_service import CompileService
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskRecord, TaskStatus, TaskTracker
from openviking.storage.queuefs import QueueManager
from openviking.storage.queuefs.external_task_processor import ExternalTaskProcessor
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.open_viking_config import CompileApiConfig
from tests.utils.mock_agfs import MockLocalAGFS


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
@pytest.mark.parametrize("finish", ["complete", "cancel", "poll_failure", "restart"])
async def test_compile_api_client_session_protocol_retry_and_cancellation(
    monkeypatch, tmp_path, finish
):
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
    request = compile_service_module.CompileRequest.model_validate(
        {
            "from": ["viking://resources/source"],
            "to": "viking://resources/wiki",
            "skill": "viking://agent/skills/wiki",
            "instruction": "Keep supporting evidence.",
            "args": {
                "model_name": "model-1",
                "user_key": "model-user-key",
                "api_key": "compile-api-key",
            },
        }
    )
    monkeypatch.setattr(service, "_normalize_request", AsyncMock(return_value=request))
    store = PersistentTaskStore(MockLocalAGFS(root_path=tmp_path))
    tracker = TaskTracker(store)
    monkeypatch.setattr(external_task_service_module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        external_task_service_module,
        "get_queue_manager",
        lambda: SimpleNamespace(enqueue=AsyncMock()),
    )
    connection = {"api_key": "active-user-key"}
    owner = {"account_id": "acct", "user_id": "alice"}
    task = await service.create(
        request,
        connection=connection,
        ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )
    assert task.to_dict()["meta"]["request"]["args"] == {"model_name": "model-1"}
    stored = await store.get(task.task_id, **owner)
    assert stored["meta"]["request"]["args"] == {"model_name": "model-1"}

    tracker = TaskTracker(store)
    restored = await tracker.get(task.task_id, **owner)
    listed = (await tracker.list_tasks(**owner))[0]
    assert restored.to_dict() == listed.to_dict() == task.to_dict()
    auth = await tracker.get_task_auth(task.task_id, **owner)
    assert auth["external_request_private"] == {
        "args": {"user_key": "model-user-key", "api_key": "compile-api-key"}
    }
    external_task_id = await service.submit(
        task.task_id,
        restored.meta["request"],
        auth["external_request_private"],
        auth["openviking_connection"],
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
    assert forwarded[0]["headers"]["Idempotency-Key"] == task.task_id
    assert forwarded[0]["headers"]["X-API-Key"] == "active-user-key"
    assert forwarded[0]["body"] == {
        "task_type": "compile",
        "payload": {
            "from": ["viking://resources/source"],
            "to": "viking://resources/wiki",
            "skill": "viking://agent/skills/wiki",
            "instruction": "Keep supporting evidence.",
            "args": {
                "model_name": "model-1",
                "user_key": "model-user-key",
                "api_key": "compile-api-key",
            },
        },
    }
    assert forwarded[1]["body"] == {"session_id": "ma-session-1"}
    assert status_snapshot.meta == {"token_usage": {"total_tokens": 12}}
    assert cancel_snapshot.status == "cancelled"
    await tracker.complete(task.task_id, {"output": "wiki"}, **owner)
    assert await tracker.get_task_auth(task.task_id, **owner) == {}

    # Exercise the real task store, work index, consumer, and ACK lifecycle.
    class QueueBackend:
        def __init__(self):
            self.pending = deque()
            self.processing = {}
            self.sequence = 0

        async def mkdir(self, path):
            pass

        async def write(self, path, data):
            if path.endswith("/enqueue"):
                self.sequence += 1
                message = {"id": str(self.sequence), "data": json.loads(data)}
                self.pending.append(message)
                return message["id"]
            assert path.endswith("/ack")
            del self.processing[data.decode()]

        async def read(self, path):
            if path.endswith("/messages"):
                result = [*self.pending, *self.processing.values()]
            elif path.endswith("/size"):
                result = len(self.pending)
            else:
                assert path.endswith("/dequeue")
                result = self.pending.popleft() if self.pending else {}
                if result:
                    self.processing[result["id"]] = result
            return json.dumps(result).encode()

    backend = QueueBackend()
    manager = QueueManager(agfs=object())
    queue = manager.get_queue(
        QueueManager.EXTERNAL_TASK,
        dequeue_handler=ExternalTaskProcessor(tasks, asyncio.get_running_loop()),
        allow_create=True,
    )
    queue._async_agfs = backend
    store = PersistentTaskStore(MockLocalAGFS(root_path=tmp_path))
    tracker = TaskTracker(store)
    monkeypatch.setattr(external_task_service_module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(external_task_service_module, "get_queue_manager", lambda: manager)
    monkeypatch.setattr(
        "openviking.storage.queuefs.external_task_processor.get_queue_manager", lambda: manager
    )
    await manager.prepare_task_tracking(tracker)
    real_sleep = asyncio.sleep

    async def yield_to_workers(_delay):
        await real_sleep(0)

    monkeypatch.setattr(external_task_service_module.asyncio, "sleep", yield_to_workers)
    entered = asyncio.Event()
    cancel_entered = asyncio.Event()
    release = asyncio.Event()
    submitted = []
    polls = 0
    rejected_id = None

    async def runtime_request(_client, method, url, headers, json):
        nonlocal polls
        if url.endswith("/runtime/v1/tasks"):
            task_id = headers["Idempotency-Key"]
            if task_id == rejected_id:
                return FakeResponse({"detail": "invalid request"}, status_code=422)
            submitted.append(task_id)
            return FakeResponse({"session_id": task_id})
        task_id = json["session_id"]
        if task_id != first.task_id:
            return FakeResponse({"status": "completed", "stage": "done", "result": {"ok": True}})
        if url.endswith("/cancel"):
            cancel_entered.set()
            await release.wait()
            return FakeResponse({"status": "cancelled", "stage": "cancelled"})
        polls += 1
        if polls == 1:
            # Runtime queueing is already submitted work, unlike OV queueing.
            return FakeResponse({"status": "pending", "stage": "queued"})
        entered.set()
        await release.wait()
        if finish == "poll_failure":
            return FakeResponse({"detail": "status unavailable"}, status_code=503)
        return FakeResponse({"status": "completed", "stage": "done", "result": {"ok": True}})

    monkeypatch.setattr(FakeClient, "request", runtime_request)

    async def create(user="alice", target="viking://resources/wiki", account="acct"):
        return await tasks.create(
            "compile",
            resource_id="viking://resources/source",
            payload={**restored.meta["request"], "to": target},
            connection={"api_key": "active-user-key"},
            ctx=RequestContext(user=UserIdentifier(account, user), role=Role.USER),
        )

    async def state(task):
        return await tracker.get(task.task_id, account_id=task.account_id, user_id=task.user_id)

    first = await create()
    waiting = await create(user="bob")
    independent = await create(target="viking://resources/other")
    cancelled = await create()
    other_account = await create(account="other-account")
    worker = asyncio.create_task(queue.dequeue())
    await asyncio.wait_for(entered.wait(), timeout=5)

    await queue.dequeue()  # Same target rotates, including across users.
    assert (await state(waiting)).status == TaskStatus.PENDING
    assert waiting.task_id not in submitted
    assert tracker.has_work(waiting.task_id)  # Requeue survives ACK of the old delivery.
    await queue.dequeue()
    assert (await state(independent)).status == TaskStatus.COMPLETED
    await tracker.cancel(cancelled.task_id, account_id="acct", user_id="alice")
    await queue.dequeue()
    assert (await state(cancelled)).status == TaskStatus.CANCELLED
    assert cancelled.task_id not in submitted
    await queue.dequeue()
    assert (await state(other_account)).status == TaskStatus.COMPLETED

    if finish == "cancel":
        await tracker.cancel(first.task_id, account_id="acct", user_id="alice")
        await asyncio.wait_for(cancel_entered.wait(), timeout=5)
        assert (await state(first)).status == TaskStatus.CANCELLING
    elif finish == "restart":
        worker.cancel()  # Process stops without requesting cancellation of the OV task.
        with pytest.raises(asyncio.CancelledError):
            await worker
        tracker = TaskTracker(store)
        tasks = ExternalTaskService()
        tasks.register(CompileService(service._config, tasks, SimpleNamespace()))
        queue.set_dequeue_handler(ExternalTaskProcessor(tasks, asyncio.get_running_loop()))
        await tasks.restore_tasks(await manager.prepare_task_tracking(tracker))
        # Recover processing behind pending work to prove recovery reserves the target first.
        backend.pending.extend(backend.processing.values())
        backend.processing.clear()

    await queue.dequeue()
    assert waiting.task_id not in submitted
    assert (await state(waiting)).status == TaskStatus.PENDING
    release.set()
    if finish == "restart":
        await queue.dequeue()
    else:
        await asyncio.wait_for(worker, timeout=5)
    expected = {
        "cancel": TaskStatus.CANCELLED,
        "poll_failure": TaskStatus.FAILED,
    }.get(finish, TaskStatus.COMPLETED)
    assert (await state(first)).status == expected
    if expected == TaskStatus.CANCELLED:
        # Recovery may replay a cancellation whose final ACK was lost.
        await tasks.cancel_recovered(first.task_id, "acct", "alice")
    if expected == TaskStatus.FAILED:
        assert "UNAVAILABLE" in (await state(first)).error
        assert not cancel_entered.is_set()
    await queue.dequeue()
    assert (await state(waiting)).status == TaskStatus.COMPLETED
    assert submitted.count(first.task_id) == 1
    assert not backend.pending and not backend.processing

    # Submission failure also releases the target for the next task.
    rejected = await create()
    rejected_id = rejected.task_id
    await queue.dequeue()
    assert (await state(rejected)).status == TaskStatus.FAILED
    after_rejection = await create()
    await queue.dequeue()
    assert (await state(after_rejection)).status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_chat_proxy_strips_client_openviking_connection_in_dev_mode(monkeypatch):
    """Dev/no-API-key proxy must not forward a browser-supplied connection.

    The Bot gateway trusts loopback openviking_connection from this proxy, so
    leaving a client-claimed identity intact would let Studio forge account/user
    /actor_peer on the --with-bot path (#4650 trust-model follow-up).
    """
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
            forwarded["json"] = json
            return FakeResponse()

    monkeypatch.setattr(bot_router_module, "BOT_API_URL", "http://127.0.0.1:18790")
    monkeypatch.setattr(bot_router_module, "BOT_API_KEY", "")
    monkeypatch.setattr(bot_router_module, "_create_bot_proxy_client", lambda: FakeClient())

    app = FastAPI()
    app.state.config = SimpleNamespace(get_effective_auth_mode=lambda: AuthMode.DEV)
    app.state.auth_plugin = DevAuthPlugin()
    app.include_router(bot_router_module.router, prefix="/bot/v1")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/bot/v1/chat",
            json={
                "message": "hello",
                "openviking_connection": {
                    "account_id": "forged-acct",
                    "user_id": "forged-user",
                    "actor_peer_id": "forged-peer",
                    "agent_id": "forged-agent",
                    "role": "root",
                    "api_key_type": "root",
                    "server_url": "http://evil.example",
                },
            },
        )

    assert response.status_code == 200
    assert "openviking_connection" not in forwarded["json"]
    assert forwarded["json"].get("user_id") == "default"


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
