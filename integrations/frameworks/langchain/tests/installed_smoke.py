# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise the built wheel without the full OpenViking package on ``sys.path``."""

from __future__ import annotations

import sys

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage

from langchain_openviking import (
    InMemoryOpenVikingClient,
    OpenVikingContextMiddleware,
    OpenVikingRetriever,
)


def main() -> None:
    """Run one retrieval and one agent turn through the installed distribution."""
    client = InMemoryOpenVikingClient(
        {"viking://resources/runbooks/deploy.md": "Deploy production with ArgoCD."}
    )
    retriever = OpenVikingRetriever(client=client, target_uri="viking://resources")

    documents = retriever.invoke("How do we deploy production?")

    assert documents
    assert "ArgoCD" in documents[0].page_content

    middleware = OpenVikingContextMiddleware(
        client=client,
        session_id_resolver=lambda _state, _runtime: "installed-wheel-smoke",
    )
    agent = create_agent(
        model=FakeListChatModel(responses=["Stored by the installed wheel."]),
        tools=[],
        middleware=[middleware],
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Remember this installed-wheel turn.")]},
        config={"configurable": {"thread_id": "installed-wheel-smoke"}},
    )

    assert result["messages"][-1].content == "Stored by the installed wheel."
    assert [message["role"] for message in client.sessions["installed-wheel-smoke"]] == [
        "user",
        "assistant",
    ]
    assert "openviking" not in sys.modules
    assert not any(
        name == "openviking_cli" or name.startswith("openviking_cli.") for name in sys.modules
    )


if __name__ == "__main__":
    main()
