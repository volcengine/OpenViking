# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for OpenAPI HTTP auth requirements."""

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from vikingbot.bus.events import OutboundEventType, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.openapi import OpenAPIChannel, OpenAPIChannelConfig, PendingResponse
from vikingbot.channels.openapi_models import ChatResponse
from vikingbot.config.schema import BotChannelConfig, SessionKey


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def message_bus():
    return MessageBus()


def _make_client(channel: OpenAPIChannel) -> TestClient:
    app = FastAPI()
    app.include_router(channel.get_router(), prefix="/bot/v1")
    return TestClient(app)


class _AsyncBytesStream(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class TestOpenAPIAuth:
    def test_health_remains_available_without_api_key(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(channel)

        response = client.get("/bot/v1/health")

        assert response.status_code == 200

    def test_public_gateway_health_requires_gateway_token(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=SimpleNamespace(
                gateway=SimpleNamespace(host="0.0.0.0", token="gateway-secret")
            ),
        )
        app = FastAPI()
        app.include_router(channel.get_router(), prefix="/bot/v1")
        app.include_router(channel.get_gateway_router())
        client = TestClient(app)

        root_challenge = client.get("/health")
        bot_challenge = client.get("/bot/v1/health")
        assert root_challenge.status_code == 401
        assert bot_challenge.status_code == 401
        assert root_challenge.headers["X-VikingBot-Gateway"] == "true"
        assert bot_challenge.headers["X-VikingBot-Gateway"] == "true"
        headers = {"X-Gateway-Token": "gateway-secret"}
        assert client.get("/health", headers=headers).status_code == 200
        assert client.get("/bot/v1/health", headers=headers).status_code == 200

    def test_trusted_sessions_are_isolated_by_request_identity(
        self, message_bus, temp_workspace, monkeypatch
    ):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="trusted",
                api_key_type="root",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        async def fake_health(request):
            return {
                "status": "ok",
                "auth_mode": "trusted",
                "role": "user",
                "account_id": request.headers["X-OpenViking-Account"],
                "user_id": request.headers["X-OpenViking-User"],
            }

        monkeypatch.setattr(channel, "_request_upstream_health", fake_health)
        client = _make_client(channel)
        alice_headers = {
            "X-API-Key": "root-key",
            "X-OpenViking-Account": "acct",
            "X-OpenViking-User": "alice",
        }
        bob_headers = {
            "X-API-Key": "root-key",
            "X-OpenViking-Account": "acct",
            "X-OpenViking-User": "bob",
        }

        created = client.post("/bot/v1/sessions", headers=alice_headers, json={})
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        assert (
            client.get(f"/bot/v1/sessions/{session_id}", headers=alice_headers).status_code == 200
        )
        assert client.get(f"/bot/v1/sessions/{session_id}", headers=bob_headers).status_code == 404
        assert client.get("/bot/v1/sessions", headers=bob_headers).json()["total"] == 0

    def test_chat_accepts_requests_when_api_key_not_configured(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )

        async def fake_handle_chat(request):
            return ChatResponse(
                session_id=request.session_id or "default", message="ok", events=None
            )

        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post("/bot/v1/chat", json={"message": "hello"})

        assert response.status_code == 200
        assert response.json()["message"] == "ok"

    def test_chat_accepts_request_with_configured_valid_api_key(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=SimpleNamespace(gateway=SimpleNamespace(token="secret123")),
        )

        async def fake_handle_chat(request):
            return ChatResponse(
                session_id=request.session_id or "default", message="ok", events=None
            )

        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-Gateway-Token": "secret123"},
            json={"message": "hello"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == "ok"

    def test_gateway_health_reports_upstream_sources(
        self, message_bus, temp_workspace, monkeypatch
    ):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="api_key",
                api_key_type="user",
                _source="inherited",
                _api_key_source="bot.ov_server.api_key",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        async def fake_health(_request):
            return {
                "status": "ok",
                "auth_mode": "api_key",
                "role": "user",
                "account_id": "acct",
                "user_id": "alice",
            }

        monkeypatch.setattr(channel, "_request_upstream_health", fake_health)
        app = FastAPI()
        app.include_router(channel.get_gateway_router())
        client = TestClient(app)

        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "openviking_inherited"
        assert body["upstream_source"] == "inherited"
        assert body["upstream_api_key_source"] == "bot.ov_server.api_key"
        assert body["gateway_token_required"] is False

    def test_gateway_health_validates_and_returns_caller_identity(
        self, message_bus, temp_workspace, monkeypatch
    ):
        captured = []
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="api_key",
                api_key_type="user",
                api_key="bot-user-key",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                captured.append(dict(headers or {}))
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "auth_mode": "api_key",
                        "role": "user",
                        "account_id": "acct",
                        "user_id": "alice",
                    },
                )

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        app = FastAPI()
        app.include_router(channel.get_gateway_router())
        client = TestClient(app)

        anonymous = client.get("/health")
        authenticated = client.get("/health", headers={"X-API-Key": "caller-user-key"})

        assert anonymous.status_code == 200
        assert "role" not in anonymous.json()
        assert captured[0]["X-API-Key"] == "bot-user-key"
        assert authenticated.status_code == 200
        assert captured[1]["X-API-Key"] == "caller-user-key"
        assert authenticated.json()["role"] == "user"
        assert authenticated.json()["account_id"] == "acct"
        assert authenticated.json()["user_id"] == "alice"

    def test_chat_rejects_when_non_localhost_and_token_not_configured(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=SimpleNamespace(gateway=SimpleNamespace(host="0.0.0.0", token="")),
        )
        client = _make_client(channel)

        response = client.post("/bot/v1/chat", json={"message": "hello"})

        assert response.status_code == 503
        assert (
            response.json()["detail"]
            == "OpenAPI gateway token is required when host is non-localhost"
        )

    def test_chat_rejects_untrusted_openviking_connection_body(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            json={
                "message": "hello",
                "openviking_connection": {
                    "api_key": "stolen-key",
                    "account_id": "acct",
                    "user_id": "alice",
                    "server_url": "http://ov.local",
                },
            },
        )

        assert response.status_code == 403
        assert "openviking_connection is only accepted" in response.json()["detail"]

    def test_chat_resolves_openviking_api_key_identity(
        self, message_bus, temp_workspace, monkeypatch
    ):
        captured = {}
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="api_key",
                api_key_type="user",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                captured["health_url"] = url
                captured["health_headers"] = headers
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "auth_mode": "api_key",
                        "role": "user",
                        "account_id": "acct",
                        "user_id": "alice",
                    },
                )

        async def fake_handle_chat(request):
            captured["connection"] = request.openviking_connection.model_dump(exclude_none=True)
            captured["sender_id"] = channel._request_user_id(request)
            captured["actor_peer_id"] = channel._request_actor_peer_id(
                request, captured["sender_id"]
            )
            return ChatResponse(
                session_id=request.session_id or "default", message="ok", events=None
            )

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-API-Key": "user-key", "X-OpenViking-Actor-Peer": "peer-a"},
            json={"message": "hello", "user_id": "display-user"},
        )

        assert response.status_code == 200
        assert captured["health_url"] == "http://ov.local/health"
        assert captured["health_headers"]["X-API-Key"] == "user-key"
        assert captured["sender_id"] == "display-user"
        assert captured["actor_peer_id"] == "peer-a"
        assert captured["connection"] == {
            "api_key": "user-key",
            "account_id": "acct",
            "user_id": "alice",
            "agent_id": "web-playground",
            "role": "user",
            "api_key_type": "user",
            "namespace_policy": {
                "isolate_user_scope_by_agent": False,
                "isolate_agent_scope_by_user": False,
            },
            "server_url": "http://ov.local",
            "actor_peer_id": "peer-a",
        }

    def test_chat_rejects_root_openviking_api_key_identity(
        self, message_bus, temp_workspace, monkeypatch
    ):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="api_key",
                api_key_type="user",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "auth_mode": "api_key",
                        "role": "root",
                        "account_id": "acct",
                        "user_id": "root",
                    },
                )

        async def fake_handle_chat(_request):
            raise AssertionError("chat handler should not run for root API key")

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-API-Key": "root-key"},
            json={"message": "hello"},
        )

        assert response.status_code == 401
        assert "User/Admin identity" in response.json()["detail"]

    def test_chat_rejects_api_key_upstream_without_openviking_api_key(
        self, message_bus, temp_workspace, monkeypatch
    ):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="0.0.0.0", token="gateway-secret"),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="api_key",
                api_key_type="user",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        async def fake_handle_chat(_request):
            raise AssertionError("chat handler should not run without an OpenViking API key")

        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-Gateway-Token": "gateway-secret"},
            json={"message": "hello"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "OpenViking API key header required"

    def test_chat_rejects_trusted_upstream_without_identity(
        self, message_bus, temp_workspace, monkeypatch
    ):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="trusted",
                api_key_type="root",
                api_key="configured-root-key",
                account_id="configured-account",
                admin_user_id="configured-user",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                return httpx.Response(
                    200,
                    json={"status": "ok", "auth_mode": "trusted"},
                    headers={"content-type": "application/json"},
                )

        async def fake_handle_chat(_request):
            raise AssertionError("chat handler should not run without trusted identity")

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-API-Key": "root-key"},
            json={"message": "hello"},
        )

        assert response.status_code == 401
        assert "Trusted OpenViking chat requires" in response.json()["detail"]
        assert "X-OpenViking-Account" in response.json()["detail"]
        assert "X-OpenViking-User" in response.json()["detail"]

    def test_chat_resolves_trusted_connection_from_request_identity(
        self, message_bus, temp_workspace, monkeypatch
    ):
        captured = {}
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="trusted",
                api_key_type="root",
                api_key="configured-root-key",
                account_id="",
                admin_user_id="",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                captured["health_headers"] = headers
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "auth_mode": "trusted",
                        "role": "user",
                        "account_id": "acct",
                        "user_id": "alice",
                    },
                    headers={"content-type": "application/json"},
                )

        async def fake_handle_chat(request):
            captured["connection"] = request.openviking_connection.model_dump(exclude_none=True)
            return ChatResponse(
                session_id=request.session_id or "default", message="ok", events=None
            )

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={
                "X-API-Key": "root-key",
                "X-OpenViking-Account": "acct",
                "X-OpenViking-User": "alice",
            },
            json={"message": "hello"},
        )

        assert response.status_code == 200
        assert captured["health_headers"]["X-API-Key"] == "root-key"
        assert captured["health_headers"]["X-OpenViking-Account"] == "acct"
        assert captured["health_headers"]["X-OpenViking-User"] == "alice"
        assert captured["connection"]["api_key"] == "root-key"
        assert captured["connection"]["account_id"] == "acct"
        assert captured["connection"]["user_id"] == "alice"
        assert captured["connection"]["api_key_type"] == "root"

    def test_chat_rejects_trusted_identity_without_request_api_key(
        self, message_bus, temp_workspace, monkeypatch
    ):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="trusted",
                api_key_type="root",
                api_key="configured-root-key",
                account_id="configured-account",
                admin_user_id="configured-user",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                return httpx.Response(
                    200,
                    json={"status": "ok", "auth_mode": "trusted"},
                    headers={"content-type": "application/json"},
                )

        async def fake_handle_chat(_request):
            raise AssertionError("chat handler should not run without a request API key")

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={
                "X-OpenViking-Account": "acct",
                "X-OpenViking-User": "alice",
            },
            json={"message": "hello"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "OpenViking API key header required"

    def test_public_gateway_requires_gateway_token_even_with_openviking_api_key(
        self, message_bus, temp_workspace, monkeypatch
    ):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="0.0.0.0", token="gateway-secret"),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="api_key",
                api_key_type="user",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        async def fake_handle_chat(_request):
            raise AssertionError("chat handler should not run without gateway token")

        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-API-Key": "user-key"},
            json={"message": "hello"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "X-Gateway-Token header required"
        assert response.headers["X-VikingBot-Gateway"] == "true"

    def test_gateway_proxy_reports_standalone_without_openviking(self, message_bus, temp_workspace):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(server_url=""),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )
        app = FastAPI()
        app.include_router(channel.get_gateway_router())
        client = TestClient(app)

        response = client.get("/api/v1/system/status")

        assert response.status_code == 503
        assert (
            response.json()["detail"]
            == "VikingBot gateway proxy is active, but no available OpenViking server is configured"
        )

    def test_chat_rejects_runtime_upstream_dev_on_public_gateway(
        self, message_bus, temp_workspace, monkeypatch
    ):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="0.0.0.0", token="gateway-secret"),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="api_key",
                api_key_type="user",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                return httpx.Response(
                    200,
                    json={"status": "ok", "auth_mode": "dev"},
                    headers={"content-type": "application/json"},
                )

        async def fake_handle_chat(_request):
            raise AssertionError("chat handler should not run against unsafe dev upstream")

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(channel, "_handle_chat", fake_handle_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat",
            headers={"X-Gateway-Token": "gateway-secret", "X-API-Key": "user-key"},
            json={"message": "hello"},
        )

        assert response.status_code == 403
        assert (
            response.json()["detail"]
            == "OpenViking server auth_mode changed to dev, but dev auth can only be used when gateway and OpenViking server are localhost"
        )

    def test_gateway_proxy_forwards_openviking_request_without_gateway_token(
        self, message_bus, temp_workspace, monkeypatch
    ):
        captured = {}
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token="secret123"),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="trusted",
                api_key_type="root",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                captured["health_url"] = url
                captured["health_headers"] = headers
                return httpx.Response(
                    200,
                    json={"status": "ok", "auth_mode": "trusted"},
                    headers={"content-type": "application/json"},
                )

            def build_request(self, method, url, content=None, headers=None):
                captured["method"] = method
                captured["url"] = url
                captured["headers"] = headers
                return httpx.Request(method, url, content=content, headers=headers)

            async def send(self, request, stream=False):
                captured["content"] = b"".join([chunk async for chunk in request.stream])
                upstream_stream = _AsyncBytesStream(b'{"ok":', b"true}")
                captured["upstream_stream"] = upstream_stream
                return httpx.Response(
                    201,
                    stream=upstream_stream,
                    headers={"content-type": "application/json"},
                    request=request,
                )

            async def aclose(self):
                captured["proxy_client_closed"] = True

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        app = FastAPI()
        app.include_router(channel.get_gateway_router())
        client = TestClient(app)

        response = client.post(
            "/api/v1/search/search?profile=1",
            headers={
                "X-Gateway-Token": "secret123",
                "X-API-Key": "user-key",
                "X-OpenViking-Account": "acct",
                "Content-Type": "application/json",
            },
            json={"query": "hello"},
        )

        assert response.status_code == 201
        assert response.json() == {"ok": True}
        assert captured["health_url"] == "http://ov.local/health"
        assert captured["method"] == "POST"
        assert captured["url"] == "http://ov.local/api/v1/search/search?profile=1"
        assert json.loads(captured["content"]) == {"query": "hello"}
        forwarded_headers = {key.lower(): value for key, value in captured["headers"].items()}
        assert forwarded_headers["x-api-key"] == "user-key"
        assert forwarded_headers["x-openviking-account"] == "acct"
        assert "x-gateway-token" not in forwarded_headers
        assert captured["upstream_stream"].closed is True
        assert captured["proxy_client_closed"] is True

    def test_gateway_proxy_does_not_add_trusted_identity_from_config(
        self, message_bus, temp_workspace, monkeypatch
    ):
        captured = {}
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", token=""),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="trusted",
                api_key_type="root",
                api_key="configured-root-key",
                account_id="acct",
                admin_user_id="alice",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def aclose(self):
                captured["proxy_client_closed"] = True

            async def get(self, url, headers=None):
                captured["health_headers"] = headers
                return httpx.Response(
                    200,
                    json={"status": "ok", "auth_mode": "trusted"},
                    headers={"content-type": "application/json"},
                )

            def build_request(self, method, url, content=None, headers=None):
                captured["content"] = content
                captured["headers"] = headers
                return httpx.Request(method, url, content=content, headers=headers)

            async def send(self, request, stream=False):
                upstream_stream = _AsyncBytesStream(b'{"ok":true}')
                captured["upstream_stream"] = upstream_stream
                return httpx.Response(
                    200,
                    stream=upstream_stream,
                    headers={"content-type": "application/json"},
                    request=request,
                )

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        app = FastAPI()
        app.include_router(channel.get_gateway_router())
        client = TestClient(app)

        response = client.get("/api/v1/system/status")

        assert response.status_code == 200
        assert captured["content"] is None
        assert captured["health_headers"] == {}
        forwarded_headers = {key.lower(): value for key, value in captured["headers"].items()}
        assert "x-api-key" not in forwarded_headers
        assert "authorization" not in forwarded_headers
        assert "x-openviking-account" not in forwarded_headers
        assert "x-openviking-user" not in forwarded_headers
        assert captured["upstream_stream"].closed is True
        assert captured["proxy_client_closed"] is True

    def test_gateway_proxy_rejects_runtime_upstream_dev_on_public_gateway(
        self, message_bus, temp_workspace, monkeypatch
    ):
        config = SimpleNamespace(
            gateway=SimpleNamespace(host="0.0.0.0", token="gateway-secret"),
            ov_server=SimpleNamespace(
                server_url="http://ov.local",
                effective_auth_mode="api_key",
                api_key_type="user",
            ),
        )
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=config,
        )

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url, headers=None):
                return httpx.Response(
                    200,
                    json={"status": "ok", "auth_mode": "dev"},
                    headers={"content-type": "application/json"},
                )

            async def request(self, *args, **kwargs):
                raise AssertionError("proxy should not forward requests to unsafe dev upstream")

        monkeypatch.setattr("vikingbot.channels.openapi.httpx.AsyncClient", FakeAsyncClient)
        app = FastAPI()
        app.include_router(channel.get_gateway_router())
        client = TestClient(app)

        response = client.get(
            "/api/v1/system/status",
            headers={"X-Gateway-Token": "gateway-secret"},
        )

        assert response.status_code == 403
        assert (
            response.json()["detail"]
            == "OpenViking server auth_mode changed to dev, but dev auth can only be used when gateway and OpenViking server are localhost"
        )

    def test_bot_channel_accepts_requests_without_channel_api_key(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        channel._bot_configs["alpha"] = BotChannelConfig(id="alpha", api_key="")

        async def fake_handle_bot_chat(channel_id, request):
            return ChatResponse(
                session_id=request.session_id or "default", message=f"ok:{channel_id}"
            )

        monkeypatch.setattr(channel, "_handle_bot_chat", fake_handle_bot_chat)
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/chat/channel",
            json={"message": "hello", "channel_id": "alpha"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == "ok:alpha"

    def test_bot_channel_allows_localhost_without_gateway_token_when_configured(
        self, message_bus, temp_workspace, monkeypatch
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
            global_config=SimpleNamespace(gateway=SimpleNamespace(token="secret123")),
        )
        channel._bot_configs["alpha"] = BotChannelConfig(id="alpha", api_key="bot-secret")

        async def fake_handle_bot_chat(channel_id, request):
            return ChatResponse(
                session_id=request.session_id or "default", message=f"ok:{channel_id}"
            )

        monkeypatch.setattr(channel, "_handle_bot_chat", fake_handle_bot_chat)
        client = _make_client(channel)

        local_without_token = client.post(
            "/bot/v1/chat/channel",
            json={"message": "hello", "channel_id": "alpha"},
        )
        assert local_without_token.status_code == 200
        assert local_without_token.json()["message"] == "ok:alpha"

        invalid = client.post(
            "/bot/v1/chat/channel",
            headers={"X-Gateway-Token": "wrong"},
            json={"message": "hello", "channel_id": "alpha"},
        )
        assert invalid.status_code == 200
        assert invalid.json()["message"] == "ok:alpha"

        authorized = client.post(
            "/bot/v1/chat/channel",
            headers={"X-Gateway-Token": "secret123"},
            json={"message": "hello", "channel_id": "alpha"},
        )
        assert authorized.status_code == 200
        assert authorized.json()["message"] == "ok:alpha"

    @pytest.mark.asyncio
    async def test_send_tracks_response_id_in_final_openapi_response(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        pending = PendingResponse()
        channel._pending["session-1"] = pending

        await channel.send(
            OutboundMessage(
                session_key=SessionKey(type="cli", channel_id="default", chat_id="session-1"),
                content="hello",
                event_type=OutboundEventType.RESPONSE,
                response_id="resp-123",
                metadata={"relevant_memories": "memory"},
            )
        )

        assert pending.final_content == "hello"
        assert pending.response_id == "resp-123"
        assert pending.relevant_memories == "memory"
        assert len(pending.events) == 1
        assert pending.events[0]["type"] == "response"
        assert pending.events[0]["data"] == {"content": "hello", "response_id": "resp-123"}

    def test_feedback_requires_existing_response(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "missing-session",
                "response_id": "missing-response",
                "feedback_type": "thumb_down",
            },
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Response not found"

    def test_rating_feedback_requires_feedback_score(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        client = _make_client(channel)

        response = client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "session-1",
                "response_id": "resp-123",
                "feedback_type": "rating",
            },
        )

        assert response.status_code == 422
        assert "feedback_score is required when feedback_type is rating" in response.text

    def test_feedback_reloads_session_after_stale_cached_miss(self, message_bus, temp_workspace):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")

        stale_session = channel._session_manager.get_or_create(session_key)
        assert stale_session.messages == []

        writer_manager = channel._session_manager.__class__(channel._session_manager.bot_data_path)
        writer_session = writer_manager.get_or_create(session_key)
        writer_session.add_message(
            "assistant",
            "hello",
            sender_id="user-1",
            response_id="resp-123",
            timestamp="2026-04-30T00:00:00",
        )
        asyncio.run(writer_manager.save(writer_session))

        client = _make_client(channel)
        response = client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "session-1",
                "response_id": "resp-123",
                "feedback_type": "thumb_up",
            },
        )

        assert response.status_code == 200
        assert response.json()["accepted"] is True

    def test_feedback_preserves_messages_written_after_stale_cache_read(
        self, message_bus, temp_workspace
    ):
        channel = OpenAPIChannel(
            OpenAPIChannelConfig(),
            message_bus,
            workspace_path=temp_workspace,
        )
        session_key = SessionKey(type="cli", channel_id="default", chat_id="session-1")

        stale_session = channel._session_manager.get_or_create(session_key)
        stale_session.add_message(
            "assistant",
            "hello",
            sender_id="user-1",
            response_id="resp-123",
            timestamp="2026-04-30T00:00:00",
        )
        asyncio.run(channel._session_manager.save(stale_session))

        writer_manager = channel._session_manager.__class__(channel._session_manager.bot_data_path)
        writer_session = writer_manager.get_or_create(session_key)
        writer_session.add_message(
            "user",
            "follow up",
            sender_id="user-1",
            timestamp="2026-04-30T00:01:00",
        )
        writer_session.add_message(
            "assistant",
            "new reply",
            sender_id="user-1",
            response_id="resp-456",
            timestamp="2026-04-30T00:02:00",
        )
        asyncio.run(writer_manager.save(writer_session))

        stale_session.metadata["local_only"] = True

        client = _make_client(channel)
        response = client.post(
            "/bot/v1/feedback",
            json={
                "session_id": "session-1",
                "response_id": "resp-123",
                "feedback_type": "thumb_up",
            },
        )

        assert response.status_code == 200

        session_path = temp_workspace / "sessions" / "cli__default__session-1.jsonl"
        lines = session_path.read_text(encoding="utf-8").splitlines()
        metadata = json.loads(lines[0])
        messages = [json.loads(line) for line in lines[1:]]

        assert metadata["metadata"]["feedback_events"][0]["response_id"] == "resp-123"
        assert "local_only" not in metadata["metadata"]
        assert [
            message.get("response_id") for message in messages if message["role"] == "assistant"
        ] == [
            "resp-123",
            "resp-456",
        ]
        assert messages[-1]["content"] == "new reply"
