from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("langchain_core")

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
    assert client.batch_sizes == [2]
    assert [message["parts"][0]["text"] for message in client.sessions["real-agent-recorder"]] == [
        "Remember this agent turn.",
        "Stored by a real LangChain agent.",
    ]
