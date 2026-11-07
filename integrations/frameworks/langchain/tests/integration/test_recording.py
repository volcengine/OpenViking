from __future__ import annotations

import copy
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_openviking")

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory

from langchain_openviking import (
    InMemoryOpenVikingClient,
    OpenVikingChatMessageHistory,
    OpenVikingCommitPolicy,
    OpenVikingContextMiddleware,
    OpenVikingSessionRecorder,
    with_openviking_context,
)


class BatchTrackingClient(InMemoryOpenVikingClient):
    def __init__(self):
        super().__init__()
        self.batch_sizes: list[int] = []

    def batch_add_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.batch_sizes.append(len(messages))
        return super().batch_add_messages(session_id, messages, **kwargs)


def test_real_runnable_with_message_history_records_each_turn_as_one_batch():
    client = BatchTrackingClient()

    def answer(messages: list[BaseMessage]) -> AIMessage:
        remembered_azure = any(
            "azure" in str(message.content).lower()
            for message in messages
            if isinstance(message, HumanMessage)
        )
        return AIMessage(content="I remember azure." if remembered_azure else "No preference yet.")

    app = RunnableWithMessageHistory(
        RunnableLambda(answer),
        lambda session_id: OpenVikingChatMessageHistory(
            session_id=session_id,
            client=client,
        ),
    )
    config = {"configurable": {"session_id": "real-history-recorder"}}

    app.invoke(
        [HumanMessage(content="Remember that the deployment color is azure.")],
        config=config,
    )
    response = app.invoke(
        [HumanMessage(content="Which deployment color do you remember?")],
        config=config,
    )

    assert response.content == "I remember azure."
    assert client.batch_sizes == [2, 2]
    assert len(client.sessions["real-history-recorder"]) == 4


def test_real_runnable_with_message_history_preserves_context_marker_text():
    client = BatchTrackingClient()
    app = RunnableWithMessageHistory(
        RunnableLambda(lambda _messages: AIMessage(content="Literal marker preserved.")),
        lambda session_id: OpenVikingChatMessageHistory(
            session_id=session_id,
            client=client,
        ),
    )

    app.invoke(
        [HumanMessage(content="How do I write <openviking_context> literally?")],
        config={"configurable": {"session_id": "real-history-marker"}},
    )

    stored = client.sessions["real-history-marker"]
    assert [message["role"] for message in stored] == ["user", "assistant"]
    assert stored[0]["parts"][0]["text"] == ("How do I write <openviking_context> literally?")


def test_real_openviking_context_wrapper_attributes_context_to_empty_assistant():
    client = BatchTrackingClient()
    client.records["viking://resources/runbooks/empty.md"] = "The deployment color is azure."

    def answer(messages: list[BaseMessage]) -> AIMessage:
        assert "The deployment color is azure." in str(messages[0].content)
        return AIMessage(content="")

    app = with_openviking_context(
        RunnableLambda(answer),
        client=client,
        session_id="real-empty-assistant",
        target_uri="viking://resources",
    )

    app.invoke([HumanMessage(content="What is the deployment color?")])

    stored = client.sessions["real-empty-assistant"]
    assert [message["role"] for message in stored] == ["user", "assistant"]
    assert not any(part["type"] == "context" for part in stored[0]["parts"])
    assert any(part["type"] == "context" for part in stored[1]["parts"])


def test_real_create_agent_records_through_middleware_recorder():
    client = BatchTrackingClient()
    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda _state, _runtime: "real-agent-recorder",
    )
    agent = create_agent(
        model=FakeListChatModel(responses=["Stored by a real LangChain agent."]),
        tools=[],
        middleware=[middleware],
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Remember this agent turn.")]},
        config={"configurable": {"thread_id": "real-agent-recorder"}},
    )

    assert result["messages"][-1].content == "Stored by a real LangChain agent."
    assert sum(client.batch_sizes) == 2
    assert [message["parts"][0]["text"] for message in client.sessions["real-agent-recorder"]] == [
        "Remember this agent turn.",
        "Stored by a real LangChain agent.",
    ]


@pytest.fixture
def recording_http_server(monkeypatch):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1,localhost,::1")
    requests: list[dict[str, Any]] = []
    failed_read = False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(
                {
                    "path": self.path,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                }
            )
            encoded = json.dumps({"status": "success", "result": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            nonlocal failed_read
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(
                {
                    "path": self.path,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "payload": payload,
                }
            )
            if self.path == "/api/v1/search/find" and not failed_read:
                failed_read = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            result = (
                {"memories": []} if self.path == "/api/v1/search/find" else {"pending_tokens": 1}
            )
            encoded = json.dumps({"status": "success", "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["cli", "env"])
@pytest.mark.parametrize("ambient_mode", ["oidc", "ldap"])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_explicit_authentication_survives_copy_recovery_and_owned_close(
    tmp_path: Path, monkeypatch, recording_http_server, source, ambient_mode, asynchronous
):
    import openviking_sdk

    for name in (
        "OPENVIKING_AUTH_MODE",
        "OPENVIKING_USERNAME",
        "OPENVIKING_PASSWORD",
        "OPENVIKING_OIDC_TOKEN",
        "OPENVIKING_ACCOUNT",
        "OPENVIKING_USER",
        "OPENVIKING_ACTOR_PEER_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    profile = {
        "auth_mode": ambient_mode,
        "ldap_username": "foreign-user",
        "ldap_password": "foreign-password",
        "oidc_token": "foreign-token",
    }
    config = tmp_path / "ovcli.conf"
    config.write_text(json.dumps(profile if source == "cli" else {}), encoding="utf-8")
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config))
    if source == "env":
        for name, value in {
            "OPENVIKING_AUTH_MODE": ambient_mode,
            "OPENVIKING_USERNAME": "foreign-user",
            "OPENVIKING_PASSWORD": "foreign-password",
            "OPENVIKING_OIDC_TOKEN": "foreign-token",
        }.items():
            monkeypatch.setenv(name, value)

    closed: list[Any] = []
    client_class = openviking_sdk.AsyncHTTPClient if asynchronous else openviking_sdk.SyncHTTPClient
    original_close = client_class.close
    if asynchronous:

        async def close(client):
            await original_close(client)
            closed.append(client)
    else:

        def close(client):
            original_close(client)
            closed.append(client)

    monkeypatch.setattr(client_class, "close", close)

    url, requests = recording_http_server
    original = OpenVikingSessionRecorder(
        url=url,
        api_key="owner-key",
        auth_mode="api_key",
        extra_headers={},
        timeout=1,
        commit_policy=OpenVikingCommitPolicy(mode="always"),
    )
    recorder = copy.deepcopy(original)
    try:
        if asynchronous:
            result = await recorder.arecord(
                "auth-session", [HumanMessage(content="Remember this.")]
            )
            client = await recorder.get_async_client()
            assert await client.find(query="read") == {"memories": []}
        else:
            result = recorder.record("auth-session", [HumanMessage(content="Remember this.")])
            assert recorder.client.find(query="read") == {"memories": []}
        assert result.messages_written == 1
        assert [request["path"] for request in requests] == [
            "/api/v1/sessions/auth-session/messages/batch",
            "/api/v1/sessions/auth-session/commit",
            "/api/v1/search/find",
            "/api/v1/search/find",
        ]
        assert all(request["headers"]["x-api-key"] == "owner-key" for request in requests)
        assert all("authorization" not in request["headers"] for request in requests)
        assert len(closed) == 1
    finally:
        if asynchronous:
            await recorder.aclose()
            await original.aclose()
        else:
            recorder.close()
            original.close()
    assert len(closed) == 2
    assert closed[0] is not closed[1]


@pytest.mark.asyncio
async def test_middleware_applies_explicit_authentication_to_all_owned_components(
    tmp_path: Path, monkeypatch, recording_http_server
):
    config = tmp_path / "ovcli.conf"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config))
    url, requests = recording_http_server
    middleware = OpenVikingContextMiddleware(
        url=url,
        api_key="owner-key",
        auth_mode="api_key",
        session_id_resolver=lambda _state, _runtime: "middleware-auth",
        include_session_context=True,
        commit_policy=OpenVikingCommitPolicy(mode="always"),
    )
    # The clients are lazy: later ambient changes must not replace the explicit
    # authentication choice when each owned component opens its transport.
    monkeypatch.setenv("OPENVIKING_AUTH_MODE", "ldap")
    monkeypatch.setenv("OPENVIKING_USERNAME", "foreign-user")
    monkeypatch.setenv("OPENVIKING_PASSWORD", "foreign-password")
    agent = create_agent(
        model=FakeListChatModel(responses=["Stored with explicit authentication."]),
        tools=[],
        middleware=[middleware],
    )
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Remember this turn.")]},
            config={"configurable": {"thread_id": "middleware-auth"}},
        )
        assert result["messages"][-1].content == "Stored with explicit authentication."
        assert any(
            request["path"].startswith("/api/v1/sessions/middleware-auth/context")
            for request in requests
        )
        assert any(request["path"] == "/api/v1/search/search" for request in requests)
        assert any(request["path"].endswith("/messages/batch") for request in requests)
        assert any(request["path"].endswith("/commit") for request in requests)
        assert all(request["headers"]["x-api-key"] == "owner-key" for request in requests)
        assert all("authorization" not in request["headers"] for request in requests)
    finally:
        await middleware.aclose()
