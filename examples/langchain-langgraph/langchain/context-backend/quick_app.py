"""Deterministic LangChain app using OpenViking as a session context backend."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from openviking.integrations.langchain import (
    InMemoryOpenVikingClient,
    OpenVikingCommitPolicy,
    with_openviking_context,
)


def build_app(client: InMemoryOpenVikingClient | None = None):
    client = client or InMemoryOpenVikingClient(
        {
            "viking://resources/runbooks/context-backend.md": (
                "OpenViking context backend examples should answer with azure."
            )
        }
    )

    def answer(messages):
        context = messages[0].content
        assert "OpenViking context backend examples" in context
        return AIMessage(content="OpenViking context says azure.")

    return with_openviking_context(
        RunnableLambda(answer),
        client=client,
        session_id="langchain-context-backend-demo",
        target_uri="viking://resources",
        commit_policy=OpenVikingCommitPolicy(
            mode="pending_tokens",
            pending_token_threshold=1_000,
        ),
    )


def main() -> str:
    app = build_app()
    result = app.invoke(
        [HumanMessage(content="What color should this context backend example use?")],
    )
    answer = result.content
    print(answer)
    return answer


if __name__ == "__main__":
    main()
