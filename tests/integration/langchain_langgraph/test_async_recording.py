from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("langchain")
pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory

from openviking.integrations.langchain import (
    InMemoryOpenVikingClient,
    OpenVikingChatMessageHistory,
    OpenVikingContextMiddleware,
    with_openviking_context,
)


class AsyncTrackingClient:
    """Native async facade for deterministic real-framework integration tests."""

    def __init__(self, records: dict[str, str] | None = None):
        self.backing = InMemoryOpenVikingClient(records)
        self.calls: list[str] = []
        self.batch_sizes: list[int] = []
        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True

    async def close(self) -> None:
        self._initialized = False

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.backing, name)
        if not callable(method):
            return method

        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            if name == "batch_add_messages":
                self.batch_sizes.append(len(kwargs["messages"]))
            return method(*args, **kwargs)

        return call


@pytest.mark.asyncio
async def test_real_runnable_with_message_history_ainvoke_uses_async_history():
    client = AsyncTrackingClient()

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        remembered_azure = any(
            "azure" in str(message.content).lower()
            for message in messages
            if isinstance(message, HumanMessage)
        )
        return AIMessage(content="I remember azure." if remembered_azure else "No preference.")

    app = RunnableWithMessageHistory(
        RunnableLambda(answer),
        lambda session_id: OpenVikingChatMessageHistory(
            session_id=session_id,
            async_client=client,
        ),
    )
    config = {"configurable": {"session_id": "real-async-history"}}

    await app.ainvoke(
        [HumanMessage(content="Remember that the deployment color is azure.")],
        config=config,
    )
    response = await app.ainvoke(
        [HumanMessage(content="Which deployment color do you remember?")],
        config=config,
    )

    assert response.content == "I remember azure."
    assert client.batch_sizes == [2, 2]
    assert len(client.backing.sessions["real-async-history"]) == 4
    assert client.calls.count("get_session_context") == 4


@pytest.mark.asyncio
async def test_real_openviking_context_wrapper_ainvoke_uses_async_lifecycle():
    client = AsyncTrackingClient(
        {"viking://resources/runbooks/async.md": "Async deployment color is teal."}
    )

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        assert "Async deployment color is teal." in str(messages[0].content)
        return AIMessage(content="OpenViking says teal.")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        session_id="real-async-wrapper",
        target_uri="viking://resources",
    )

    result = await app.ainvoke([HumanMessage(content="What is the async deployment color?")])

    assert result.content == "OpenViking says teal."
    assert client.batch_sizes == [2]
    assert {"create_session", "get_session_context", "search", "read"}.issubset(client.calls)
    assistant_parts = client.backing.sessions["real-async-wrapper"][1]["parts"]
    assert any(part["type"] == "context" for part in assistant_parts)


@pytest.mark.asyncio
async def test_real_async_context_wrapper_clears_pending_context_after_failure():
    client = AsyncTrackingClient(
        {"viking://resources/runbooks/failure.md": "Async failure context is amber."}
    )
    calls = 0

    async def answer(_messages: list[BaseMessage]) -> AIMessage:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic async model failure")
        return AIMessage(content="Recovered asynchronously.")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        session_id="real-async-failure",
        target_uri="viking://resources",
    )

    with pytest.raises(RuntimeError, match="synthetic async model failure"):
        await app.ainvoke([HumanMessage(content="What amber failure context?")])

    client.backing.records.clear()
    result = await app.ainvoke([HumanMessage(content="No matching context on this retry.")])

    assert result.content == "Recovered asynchronously."
    assistant_parts = client.backing.sessions["real-async-failure"][-1]["parts"]
    assert not any(part["type"] == "context" for part in assistant_parts)


@pytest.mark.asyncio
async def test_real_create_agent_ainvoke_uses_async_middleware_hooks():
    client = AsyncTrackingClient()
    middleware = OpenVikingContextMiddleware(
        async_client=client,
        session_id_resolver=lambda _state, _runtime: "real-async-agent",
    )
    agent = create_agent(
        model=FakeListChatModel(responses=["Stored by async middleware."]),
        tools=[],
        middleware=[middleware],
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="Remember this async agent turn.")]},
        config={"configurable": {"thread_id": "real-async-agent"}},
    )

    assert result["messages"][-1].content == "Stored by async middleware."
    assert client.batch_sizes == [2]
    assert [
        message["parts"][0]["text"] for message in client.backing.sessions["real-async-agent"]
    ] == [
        "Remember this async agent turn.",
        "Stored by async middleware.",
    ]
