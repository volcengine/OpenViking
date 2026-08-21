from __future__ import annotations

from contextlib import contextmanager
from importlib import import_module
from typing import Any, Iterator

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("langchain_openviking")

from langchain_core.messages import AIMessage, HumanMessage

_openviking_sdk = import_module("openviking_sdk")
actor_peer_module = import_module("langchain_openviking.actor_peer")
_langchain_integration = import_module("langchain_openviking")
_langchain_client = import_module("langchain_openviking.client")
InMemoryOpenVikingClient = _langchain_integration.InMemoryOpenVikingClient
OpenVikingContextMiddleware = _langchain_integration.OpenVikingContextMiddleware
OpenVikingClientHandle = _langchain_client.OpenVikingClientHandle
OpenVikingConnection = _langchain_client.OpenVikingConnection


@contextmanager
def _older_sdk_actor_peer_support(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    with monkeypatch.context() as older_sdk:
        older_sdk.setattr(actor_peer_module, "_actor_peer_getter", None)
        older_sdk.setattr(actor_peer_module, "_actor_peer_scope", None)
        yield


def test_langchain_exports_actor_peer_capability_helper():
    integration = import_module("langchain_openviking")
    assert integration.has_request_actor_peer_support()
    assert "has_request_actor_peer_support" in integration.__all__

    namespace: dict[str, Any] = {}
    exec("from langchain_openviking import *", namespace)

    assert namespace["has_request_actor_peer_support"] is (
        integration.has_request_actor_peer_support
    )
    assert "OpenVikingRequestIdentity" not in namespace


def test_langchain_star_import_remains_usable_with_older_sdk(
    monkeypatch: pytest.MonkeyPatch,
):
    integration = import_module("langchain_openviking")
    with _older_sdk_actor_peer_support(monkeypatch):
        assert not integration.has_request_actor_peer_support()

        namespace: dict[str, Any] = {}
        exec("from langchain_openviking import *", namespace)

        assert namespace["OpenVikingContextMiddleware"] is (integration.OpenVikingContextMiddleware)
        assert not namespace["has_request_actor_peer_support"]()


def test_middleware_remains_usable_with_older_sdk_when_actor_peer_is_unused(
    monkeypatch: pytest.MonkeyPatch,
):
    with _older_sdk_actor_peer_support(monkeypatch):
        middleware = OpenVikingContextMiddleware(
            client=InMemoryOpenVikingClient(),
            session_id_resolver=lambda _state, _runtime: "legacy-sdk-session",
        )
        middleware.after_agent(
            {
                "messages": [
                    HumanMessage(content="Existing middleware remains usable."),
                    AIMessage(content="Stored."),
                ]
            },
            runtime=None,
        )

        with pytest.raises(ImportError, match="Upgrade openviking-sdk"):
            OpenVikingContextMiddleware(
                actor_peer_resolver=lambda _state, _runtime: None,
            )


def test_middleware_rejects_custom_client_without_actor_peer_support():
    with pytest.raises(
        ValueError,
        match="clients that support request-scoped actor peers",
    ):
        OpenVikingContextMiddleware(
            client=InMemoryOpenVikingClient(),
            actor_peer_resolver=lambda _state, _runtime: "runtime-agent",
        )


def test_middleware_accepts_official_request_actor_peer_client():
    client = _openviking_sdk.AsyncHTTPClient(url="http://openviking.test")

    middleware = OpenVikingContextMiddleware(
        async_client=client,
        actor_peer_resolver=lambda _state, _runtime: "runtime-agent",
    )

    assert middleware.recorder._connection.async_client is client


@pytest.mark.parametrize(
    ("resolved", "error", "message"),
    [
        (123, TypeError, "actor_peer_resolver must return a string or None"),
        ("  ", ValueError, "actor_peer_resolver must not return an empty string"),
    ],
)
def test_middleware_validates_actor_peer_resolver_result(resolved, error, message):
    middleware = OpenVikingContextMiddleware(
        actor_peer_resolver=lambda _state, _runtime: resolved,
        session_id_resolver=lambda _state, _runtime: "runtime-actor-session",
    )

    with pytest.raises(error, match=message):
        middleware.after_agent(
            {
                "messages": [
                    HumanMessage(content="Remember this."),
                    AIMessage(content="Stored."),
                ]
            },
            runtime=None,
        )
