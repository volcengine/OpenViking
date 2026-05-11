"""Deterministic LangGraph smoke app using OpenViking tools and store."""

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict

from openviking.integrations.langchain import (
    InMemoryOpenVikingClient,
    OpenVikingStore,
    create_openviking_tools,
)


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    openviking_context: str


def build_app(client: InMemoryOpenVikingClient | None = None):
    client = client or InMemoryOpenVikingClient(
        {
            "viking://user/memories/profile.md": (
                "The user wants LangGraph agents to use OpenViking for durable context."
            ),
            "viking://resources/runbooks/langgraph.md": (
                "LangGraph workflows can call OpenViking tools before model nodes."
            ),
        }
    )
    store = OpenVikingStore(client=client)
    store.put(
        ("demo", "user"),
        "deployment",
        {"color": "azure", "framework": "langgraph"},
    )
    find_tool = next(
        tool
        for tool in create_openviking_tools(client=client, profile="retrieval")
        if tool.name == "viking_find"
    )
    model = FakeListChatModel(
        responses=[
            "The LangGraph workflow should use OpenViking context and azure deployment color.",
        ]
    )

    def recall(state: AgentState) -> AgentState:
        latest = state["messages"][-1].content
        context = find_tool.invoke({"query": latest, "limit": 4})
        stored = store.search(("demo",), query="azure", limit=1)
        if stored:
            context += f"\n\nStore: {stored[0].value}"
        return {"openviking_context": context}

    def answer(state: AgentState) -> AgentState:
        latest = state["messages"][-1].content
        response = model.invoke(
            [
                HumanMessage(
                    content=(
                        "OpenViking context:\n"
                        f"{state.get('openviking_context', '')}\n\nQuestion: {latest}"
                    )
                )
            ]
        )
        return {"messages": [AIMessage(content=response.content)]}

    graph = StateGraph(AgentState)
    graph.add_node("recall", recall)
    graph.add_node("answer", answer)
    graph.add_edge(START, "recall")
    graph.add_edge("recall", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def main() -> str:
    app = build_app()
    result = app.invoke(
        {"messages": [HumanMessage(content="How should LangGraph use OpenViking?")]}
    )
    answer = result["messages"][-1].content
    print(answer)
    return answer


if __name__ == "__main__":
    main()
