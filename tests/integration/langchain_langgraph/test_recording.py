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
