"""Deterministic LangGraph app using OpenViking context middleware."""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict

from openviking.integrations.langchain import (
    InMemoryOpenVikingClient,
    OpenVikingContextMiddleware,
)
from openviking.integrations.langchain.client import extract_message_text


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]


def build_app(client: InMemoryOpenVikingClient | None = None):
    client = client or InMemoryOpenVikingClient(
        {
            "viking://user/memories/profile.md": (
                "OpenViking middleware examples should answer with azure."
            )
        }
    )
    session_id = "langgraph-middleware-demo"
    middleware = OpenVikingContextMiddleware(
        client=client,
        target_uri="viking://user/memories",
        session_id_resolver=lambda _state, _runtime: session_id,
        include_active_messages=True,
    )

    class Runtime:
        config = {"configurable": {"thread_id": session_id}}

    def model_node(state: AgentState) -> AgentState:
        current_messages = list(state["messages"])

        class Request:
            state: dict[str, Any] = {}
            runtime = Runtime()
            messages: list[BaseMessage] = current_messages
            system_message = None

            def override(self, **overrides):
                new_request = Request()
                new_request.messages = overrides.get("messages", self.messages)
                new_request.system_message = overrides.get(
                    "system_message",
                    self.system_message,
                )
                return new_request

        def handler(request):
            context = (
                extract_message_text(request.system_message.content)
                if request.system_message
                else ""
            )
            if "azure" in context.lower():
                return AIMessage(content="OpenViking middleware context says azure.")
            return AIMessage(content="OpenViking middleware context was missing.")

        response = middleware.wrap_model_call(Request(), handler)
        middleware.after_agent(
            {"messages": current_messages + [response]},
            Runtime(),
        )
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("model", model_node)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    return graph.compile()


def main() -> str:
    app = build_app()
    result = app.invoke(
        {"messages": [HumanMessage(content="What should this middleware example use?")]}
    )
    answer = result["messages"][-1].content
    print(answer)
    return answer


if __name__ == "__main__":
    main()
