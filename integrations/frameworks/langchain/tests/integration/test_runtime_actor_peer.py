from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest

pytest.importorskip("langchain")
pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("langchain_openviking")

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from openviking_sdk import get_actor_peer_id, use_actor_peer

_langchain_integration = import_module("langchain_openviking")
InMemoryOpenVikingClient = _langchain_integration.InMemoryOpenVikingClient
OpenVikingContextMiddleware = _langchain_integration.OpenVikingContextMiddleware


class RuntimeActorPeerTrackingClient:
    """Async client facade that observes the active SDK actor peer."""

    supports_request_actor_peer = True

    def __init__(self, records: dict[str, str] | None = None):
        self.backing = InMemoryOpenVikingClient(records)
        self.actor_peer_calls: list[tuple[str, str | None]] = []

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.backing, name)
        if not callable(method):
            return method

        async def call(*args: Any, **kwargs: Any) -> Any:
            self.actor_peer_calls.append((name, get_actor_peer_id()))
            return method(*args, **kwargs)

        return call


@pytest.mark.asyncio
async def test_real_create_agent_resolves_actor_peer_from_runtime_context():
    client = RuntimeActorPeerTrackingClient(
        {"viking://~/memories/profile.md": "Runtime-scoped memory."}
    )

    def resolve_actor_peer(_state: dict[str, Any], runtime: Any) -> str:
        return runtime.context["actor_peer_id"]

    middleware = OpenVikingContextMiddleware(
        async_client=client,
        actor_peer_resolver=resolve_actor_peer,
        session_id_resolver=lambda _state, _runtime: "shared-thread-id",
    )
    agent = create_agent(
        model=FakeListChatModel(responses=["Stored with runtime actor peer."]),
        tools=[],
        middleware=[middleware],
        context_schema=dict,
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="Remember this agent-specific turn.")]},
        config={"configurable": {"thread_id": "shared-thread-id"}},
        context={"user_id": "alice", "actor_peer_id": "lead-agent"},
    )

    assert result["messages"][-1].content == "Stored with runtime actor peer."
    relevant_calls = [
        (name, actor_peer_id)
        for name, actor_peer_id in client.actor_peer_calls
        if name in {"search", "batch_add_messages"}
    ]
    assert relevant_calls
    assert {actor_peer_id for _, actor_peer_id in relevant_calls} == {"lead-agent"}


@pytest.mark.asyncio
async def test_middleware_capture_progress_is_isolated_by_actor_peer():
    client = RuntimeActorPeerTrackingClient()
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        actor_peer_resolver=lambda _state, runtime: runtime.context["actor_peer_id"],
        session_id_resolver=lambda _state, _runtime: "shared-thread-id",
    )
    state = {
        "messages": [
            HumanMessage(content="Identical input."),
            AIMessage(content="Identical output."),
        ]
    }

    class Runtime:
        def __init__(self, actor_peer_id: str):
            self.context = {"actor_peer_id": actor_peer_id}

    await middleware.aafter_agent(state, Runtime("assistant-a"))
    await middleware.aafter_agent(state, Runtime("assistant-b"))

    write_actor_peers = [
        actor_peer_id
        for name, actor_peer_id in client.actor_peer_calls
        if name == "batch_add_messages"
    ]
    assert write_actor_peers == ["assistant-a", "assistant-b"]


@pytest.mark.asyncio
async def test_middleware_without_resolver_preserves_ambient_actor_peer():
    client = RuntimeActorPeerTrackingClient()
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        session_id_resolver=lambda _state, _runtime: "ambient-actor-session",
    )
    state = {
        "messages": [
            HumanMessage(content="Identical ambient input."),
            AIMessage(content="Identical ambient output."),
        ]
    }

    for actor_peer_id in ("assistant-a", "assistant-b"):
        with use_actor_peer(actor_peer_id):
            await middleware.aafter_agent(state, runtime=None)

    write_actor_peers = [
        observed_actor_peer
        for name, observed_actor_peer in client.actor_peer_calls
        if name == "batch_add_messages"
    ]
    assert write_actor_peers == ["assistant-a", "assistant-b"]


@pytest.mark.asyncio
async def test_middleware_resolver_none_clears_ambient_actor_peer():
    client = RuntimeActorPeerTrackingClient()
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        actor_peer_resolver=lambda _state, _runtime: None,
        session_id_resolver=lambda _state, _runtime: "fixed-actor-session",
    )

    with use_actor_peer("ambient-agent"):
        await middleware.aafter_agent(
            {
                "messages": [
                    HumanMessage(content="Use no actor peer."),
                    AIMessage(content="Stored without an actor peer."),
                ]
            },
            runtime=None,
        )

    write_actor_peers = [
        observed_actor_peer
        for name, observed_actor_peer in client.actor_peer_calls
        if name == "batch_add_messages"
    ]
    assert write_actor_peers == [None]
