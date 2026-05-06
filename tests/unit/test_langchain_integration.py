from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage

from openviking.integrations.langchain import (
    InMemoryOpenVikingClient,
    OpenVikingContextMiddleware,
    OpenVikingRetriever,
    OpenVikingStore,
    create_openviking_tools,
)


def test_retriever_returns_langchain_documents():
    client = InMemoryOpenVikingClient(
        {
            "viking://user/memories/preferences.md": "The user prefers azure deploys.",
            "viking://resources/runbooks/release.md": "Release notes mention LangChain.",
        }
    )
    retriever = OpenVikingRetriever(
        client=client,
        target_uri=["viking://user/memories", "viking://resources"],
        limit=3,
    )

    docs = retriever.invoke("azure LangChain")

    assert {doc.metadata["openviking_uri"] for doc in docs} == {
        "viking://resources/runbooks/release.md",
        "viking://user/memories/preferences.md",
    }
    assert all(doc.page_content for doc in docs)


def test_create_openviking_tools_exposes_common_viking_primitives():
    client = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "The user likes LangGraph agents."}
    )
    tools = create_openviking_tools(client=client, profile="agent")
    names = {tool.name for tool in tools}

    assert {
        "viking_find",
        "viking_search",
        "viking_browse",
        "viking_read",
        "viking_grep",
        "viking_store",
        "viking_add_resource",
        "viking_add_skill",
        "viking_health",
    }.issubset(names)
    assert "viking_forget" not in names

    find_tool = next(tool for tool in tools if tool.name == "viking_find")
    assert "viking://user/memories/profile.md" in find_tool.invoke(
        {"query": "LangGraph", "limit": 2}
    )

    store_tool = next(tool for tool in tools if tool.name == "viking_store")
    stored = store_tool.invoke(
        {
            "messages": [
                {"role": "user", "content": "Remember that azure is preferred."},
                {"role": "assistant", "content": "Noted."},
            ],
            "session_id": "test-session",
            "commit": False,
        }
    )
    assert '"messages_added":2' in stored
    assert len(client.sessions["test-session"]) == 2


def test_langgraph_store_round_trip_and_semantic_search():
    client = InMemoryOpenVikingClient()
    store = OpenVikingStore(client=client)

    store.put(
        ("users", "ada"),
        "preferences",
        {"color": "azure", "framework": "langgraph", "nested": {"rank": 3}},
    )

    item = store.get(("users", "ada"), "preferences")
    assert item.value["framework"] == "langgraph"

    filtered = store.search(("users",), filter={"nested.rank": {"$gte": 3}}, limit=5)
    assert filtered[0].key == "preferences"

    semantic = store.search(("users",), query="azure", limit=5)
    assert semantic[0].namespace == ("users", "ada")
    assert semantic[0].value["color"] == "azure"

    assert store.list_namespaces(prefix=("users",)) == [("users", "ada")]


def test_langgraph_middleware_injects_recall_and_captures_messages():
    client = InMemoryOpenVikingClient(
        {"viking://user/memories/profile.md": "The user prefers azure deployments."}
    )
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://user/memories",
        session_id_resolver=lambda state, runtime: "middleware-session",
        commit_on_after_agent=True,
    )

    captured_request = {}

    class Request:
        messages = [HumanMessage(content="What deployment color?")]
        system_message = None

        def override(self, **overrides):
            new_request = Request()
            new_request.messages = overrides.get("messages", self.messages)
            new_request.system_message = overrides.get("system_message", self.system_message)
            return new_request

    def handler(request):
        captured_request["request"] = request
        return AIMessage(content="ok")

    middleware.wrap_model_call(Request(), handler)
    assert "OpenViking context" in captured_request["request"].system_message.content
    assert "azure deployments" in captured_request["request"].system_message.content

    middleware.after_agent(
        {
            "messages": [
                HumanMessage(content="Remember this."),
                AIMessage(content="I will."),
            ]
        },
        runtime=None,
    )
    assert len(client.sessions["middleware-session"]) == 2
