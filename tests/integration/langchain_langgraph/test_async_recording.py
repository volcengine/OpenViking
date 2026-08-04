from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("langchain")
pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("langchain_openviking")

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openviking import (
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
async def test_real_openviking_context_wrapper_preserves_concurrent_same_session_turns():
    client = AsyncTrackingClient()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        if latest_user == "from-a":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        return AIMessage(content=f"answer:{latest_user}")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        session_id="real-async-concurrent-history",
        inject_context=False,
    )

    first = asyncio.create_task(app.ainvoke([HumanMessage(content="from-a")]))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(app.ainvoke([HumanMessage(content="from-b")]))

    try:
        await asyncio.wait_for(second_started.wait(), timeout=1)
        await asyncio.wait_for(second, timeout=1)
    finally:
        release_first.set()

    await first

    assert [
        message["parts"][0]["text"]
        for message in client.backing.sessions["real-async-concurrent-history"]
    ] == [
        "from-b",
        "answer:from-b",
        "from-a",
        "answer:from-a",
    ]


@pytest.mark.asyncio
async def test_concurrent_same_session_calls_serialize_only_final_persistence():
    class BlockingWriteClient(AsyncTrackingClient):
        def __init__(self):
            super().__init__()
            self.write_calls = 0
            self.first_write_started = asyncio.Event()
            self.release_first_write = asyncio.Event()
            self.second_write_started = asyncio.Event()

        async def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.write_calls += 1
            self.calls.append("batch_add_messages")
            self.batch_sizes.append(len(messages))
            if self.write_calls == 1:
                self.first_write_started.set()
                await self.release_first_write.wait()
            else:
                self.second_write_started.set()
            return self.backing.batch_add_messages(session_id, messages, **kwargs)

    client = BlockingWriteClient()
    both_models_ready = asyncio.Event()
    model_calls = 0

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 2:
            both_models_ready.set()
        await both_models_ready.wait()
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        return AIMessage(content=f"answer:{latest_user}")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        session_id="real-async-write-lock",
        inject_context=False,
    )
    first = asyncio.create_task(app.ainvoke([HumanMessage(content="from-a")]))
    second = asyncio.create_task(app.ainvoke([HumanMessage(content="from-b")]))

    await asyncio.wait_for(both_models_ready.wait(), timeout=1)
    await asyncio.wait_for(client.first_write_started.wait(), timeout=1)
    await asyncio.sleep(0.05)

    try:
        assert client.second_write_started.is_set() is False
    finally:
        client.release_first_write.set()
    await asyncio.gather(first, second)

    assert client.second_write_started.is_set() is True
    assert client.write_calls == 2


def test_context_wrapper_write_lock_is_scoped_per_event_loop():
    class SlowWriteClient(AsyncTrackingClient):
        async def batch_add_messages(
            self,
            session_id: str,
            messages: list[dict[str, Any]],
            **kwargs: Any,
        ) -> dict[str, Any]:
            await asyncio.sleep(0.01)
            return self.backing.batch_add_messages(session_id, messages, **kwargs)

    client = SlowWriteClient()

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        return AIMessage(content=f"answer:{latest_user}")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        session_id="real-async-cross-loop-write-lock",
        inject_context=False,
    )

    async def run_contended_pair(label: str) -> None:
        results = await app.abatch(
            [
                [HumanMessage(content=f"{label}-a")],
                [HumanMessage(content=f"{label}-b")],
            ]
        )
        assert [result.content for result in results] == [
            f"answer:{label}-a",
            f"answer:{label}-b",
        ]

    asyncio.run(run_contended_pair("loop-one"))
    asyncio.run(run_contended_pair("loop-two"))

    assert len(client.backing.sessions["real-async-cross-loop-write-lock"]) == 8


@pytest.mark.asyncio
async def test_real_openviking_context_wrapper_keeps_different_sessions_concurrent():
    client = AsyncTrackingClient()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        if latest_user == "from-a":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        return AIMessage(content=f"answer:{latest_user}")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        inject_context=False,
    )

    first = asyncio.create_task(
        app.ainvoke(
            [HumanMessage(content="from-a")],
            config={"configurable": {"session_id": "real-async-session-a"}},
        )
    )
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(
        app.ainvoke(
            [HumanMessage(content="from-b")],
            config={"configurable": {"session_id": "real-async-session-b"}},
        )
    )

    try:
        await asyncio.wait_for(second_started.wait(), timeout=1)
    finally:
        release_first.set()

    await asyncio.gather(first, second)

    assert len(client.backing.sessions["real-async-session-a"]) == 2
    assert len(client.backing.sessions["real-async-session-b"]) == 2


@pytest.mark.asyncio
async def test_concurrent_same_session_invocations_keep_peer_attribution_local():
    client = AsyncTrackingClient()
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        if latest_user == "from-a":
            first_started.set()
            await release_first.wait()
        return AIMessage(content=f"answer:{latest_user}")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        session_id="real-async-peer-attribution",
        inject_context=False,
    )
    first = asyncio.create_task(
        app.ainvoke(
            [HumanMessage(content="from-a")],
            config={"configurable": {"peer_id": "peer-a"}},
        )
    )
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(
        app.ainvoke(
            [HumanMessage(content="from-b")],
            config={"configurable": {"peer_id": "peer-b"}},
        )
    )

    try:
        await asyncio.wait_for(second, timeout=1)
    finally:
        release_first.set()
    await first

    stored = client.backing.sessions["real-async-peer-attribution"]
    by_text = {message["parts"][0]["text"]: message.get("peer_id") for message in stored}
    assert by_text == {
        "from-a": "peer-a",
        "answer:from-a": "peer-a",
        "from-b": "peer-b",
        "answer:from-b": "peer-b",
    }


@pytest.mark.asyncio
async def test_concurrent_same_session_invocations_keep_recalled_context_local():
    alpha_uri = "viking://resources/runbooks/alpha.md"
    beta_uri = "viking://resources/runbooks/beta.md"
    client = AsyncTrackingClient(
        {
            alpha_uri: "Alpha deployment instructions.",
            beta_uri: "Beta deployment instructions.",
        }
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        if latest_user == "alpha":
            first_started.set()
            await release_first.wait()
        return AIMessage(content=f"answer:{latest_user}")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        session_id="real-async-context-attribution",
        target_uri="viking://resources",
    )
    first = asyncio.create_task(app.ainvoke([HumanMessage(content="alpha")]))
    await asyncio.wait_for(first_started.wait(), timeout=1)

    try:
        await asyncio.wait_for(
            app.ainvoke([HumanMessage(content="beta")]),
            timeout=1,
        )
    finally:
        release_first.set()
    await first

    context_uri_by_answer: dict[str, str] = {}
    for message in client.backing.sessions["real-async-context-attribution"]:
        if message["role"] != "assistant":
            continue
        answer_text = next(part["text"] for part in message["parts"] if part["type"] == "text")
        context_uri = next(part["uri"] for part in message["parts"] if part["type"] == "context")
        context_uri_by_answer[answer_text] = context_uri

    assert context_uri_by_answer == {
        "answer:alpha": alpha_uri,
        "answer:beta": beta_uri,
    }


@pytest.mark.asyncio
async def test_concurrent_session_failure_keeps_other_session_context_attribution():
    client = AsyncTrackingClient(
        {"viking://resources/runbooks/concurrent.md": "Concurrent context is violet."}
    )
    second_started = asyncio.Event()
    release_second = asyncio.Event()

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        if latest_user == "fail-a":
            raise RuntimeError("session a failed")
        second_started.set()
        await release_second.wait()
        return AIMessage(content="answer:from-b")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        target_uri="viking://resources",
    )
    second = asyncio.create_task(
        app.ainvoke(
            [HumanMessage(content="violet")],
            config={"configurable": {"session_id": "real-async-context-b"}},
        )
    )
    await asyncio.wait_for(second_started.wait(), timeout=1)

    try:
        with pytest.raises(RuntimeError, match="session a failed"):
            await app.ainvoke(
                [HumanMessage(content="fail-a")],
                config={"configurable": {"session_id": "real-async-context-a"}},
            )
    finally:
        release_second.set()
    await second

    assistant_parts = client.backing.sessions["real-async-context-b"][-1]["parts"]
    assert any(part["type"] == "context" for part in assistant_parts)


@pytest.mark.asyncio
async def test_real_openviking_context_wrapper_preserves_same_session_abatch_turns():
    client = AsyncTrackingClient()

    async def answer(messages: list[BaseMessage]) -> AIMessage:
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        await asyncio.sleep(0)
        return AIMessage(content=f"answer:{latest_user}")

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        session_id="real-async-batch-history",
        inject_context=False,
    )

    results = await app.abatch(
        [
            [HumanMessage(content="from-a")],
            [HumanMessage(content="from-b")],
        ]
    )

    assert [result.content for result in results] == ["answer:from-a", "answer:from-b"]
    stored = [
        message["parts"][0]["text"]
        for message in client.backing.sessions["real-async-batch-history"]
    ]
    assert len(stored) == 4
    assert {tuple(stored[index : index + 2]) for index in range(0, len(stored), 2)} == {
        ("from-a", "answer:from-a"),
        ("from-b", "answer:from-b"),
    }


@pytest.mark.asyncio
async def test_abandoned_stream_does_not_block_same_session_invocation():
    client = AsyncTrackingClient()
    keep_stream_open = asyncio.Event()

    async def answer(messages: list[BaseMessage]):
        latest_user = next(
            message.content for message in reversed(messages) if isinstance(message, HumanMessage)
        )
        yield AIMessage(content=f"answer:{latest_user}")
        if latest_user == "stream":
            await keep_stream_open.wait()

    app = with_openviking_context(
        RunnableLambda(answer),
        async_client=client,
        session_id="real-async-abandoned-stream",
        inject_context=False,
    )
    stream = app.astream([HumanMessage(content="stream")])
    first_chunk = await anext(stream)

    try:
        result = await asyncio.wait_for(
            app.ainvoke([HumanMessage(content="next")]),
            timeout=1,
        )
    finally:
        await stream.aclose()

    assert first_chunk.content == "answer:stream"
    assert result.content == "answer:next"
    assert [
        message["parts"][0]["text"]
        for message in client.backing.sessions["real-async-abandoned-stream"]
    ] == ["next", "answer:next"]


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
