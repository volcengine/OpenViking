"""Deterministic LangChain app using OpenViking-backed chat history."""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory

from openviking.integrations.langchain import (
    InMemoryOpenVikingClient,
    OpenVikingChatMessageHistory,
)
from openviking.integrations.langchain.client import extract_message_text


def build_app(client: InMemoryOpenVikingClient | None = None):
    client = client or InMemoryOpenVikingClient()

    def answer(messages: list[BaseMessage]) -> AIMessage:
        text = "\n".join(extract_message_text(message.content) for message in messages)
        if "azure" in text.lower():
            return AIMessage(content="OpenViking history remembers azure.")
        return AIMessage(content="OpenViking history is waiting for a preference.")

    return RunnableWithMessageHistory(
        RunnableLambda(answer),
        lambda session_id: OpenVikingChatMessageHistory(
            session_id=session_id,
            client=client,
        ),
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
