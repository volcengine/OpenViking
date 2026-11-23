"""Deterministic host-owned chat history with OpenViking memory capture."""

from __future__ import annotations

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_openviking import (
    InMemoryOpenVikingClient,
    with_openviking_memory,
)
from langchain_openviking.client import extract_message_text


def build_app(client: InMemoryOpenVikingClient | None = None):
    client = client or InMemoryOpenVikingClient()

    def answer(messages: list[BaseMessage]) -> AIMessage:
        text = "\n".join(extract_message_text(message.content) for message in messages)
        if "azure" in text.lower():
            return AIMessage(content="OpenViking history remembers azure.")
        return AIMessage(content="OpenViking history is waiting for a preference.")

    # Use a durable host history provider in production (see native_history.py).
    histories = {}
    return with_openviking_memory(
        RunnableLambda(answer),
        client=client,
        history_factory=lambda sid: histories.setdefault(sid, InMemoryChatMessageHistory()),
    )


def main() -> str:
    app = build_app()
    config = {"configurable": {"session_id": "langchain-history-demo"}}

    app.invoke(
        [HumanMessage(content="Remember that the deployment color is azure.")],
        config=config,
    )
    result = app.invoke(
        [HumanMessage(content="Which deployment color did I ask you to remember?")],
        config=config,
    )
    answer = result.content
    print(answer)
    return answer


if __name__ == "__main__":
    main()
