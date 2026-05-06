"""Live LangGraph lane using OpenViking context and an OpenAI-compatible LLM.

Required:
  OPENVIKING_LANGGRAPH_LIVE=1
  ARK_API_KEY

Optional:
  ARK_BASE_URL, ARK_MODEL
  OPENVIKING_LIVE_BACKEND=memory|http|local
  OPENVIKING_URL, OPENVIKING_API_KEY, OPENVIKING_PATH
"""

import os
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from openai import OpenAI
from typing_extensions import Annotated, TypedDict

from openviking.integrations.langchain import InMemoryOpenVikingClient, create_openviking_tools


class LiveState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    openviking_context: str


def build_context_client():
    backend = os.environ.get("OPENVIKING_LIVE_BACKEND", "memory").lower()
    if backend == "http":
        from openviking.client import SyncHTTPClient

        client = SyncHTTPClient(
            url=os.environ["OPENVIKING_URL"],
            api_key=os.environ.get("OPENVIKING_API_KEY"),
            user_id=os.environ.get("OPENVIKING_USER_ID"),
            agent_id=os.environ.get("OPENVIKING_AGENT_ID"),
        )
        client.initialize()
        return client
    if backend == "local":
        from openviking.sync_client import SyncOpenViking

        client = SyncOpenViking(path=os.environ.get("OPENVIKING_PATH"))
        client.initialize()
        return client
    return InMemoryOpenVikingClient(
        {
            "viking://user/memories/live/langgraph.md": (
                "The live LangGraph smoke lane should answer that OpenViking supplies "
                "agent context before the LLM call."
            )
        }
    )


def seed_context(client) -> None:
    if isinstance(client, InMemoryOpenVikingClient):
        return
    uri = f"viking://resources/live/langgraph-smoke-{uuid.uuid4().hex}.md"
    content = (
        "The live LangGraph smoke lane should answer that OpenViking supplies "
        "agent context before the LLM call."
    )
    client.write(uri=uri, content=content, mode="create", wait=True, timeout=60)


def build_app():
    context_client = build_context_client()
    seed_context(context_client)
    find_tool = next(
        tool for tool in create_openviking_tools(client=context_client, profile="retrieval")
        if tool.name == "viking_find"
    )
    llm = OpenAI(
        api_key=os.environ["ARK_API_KEY"],
        base_url=os.environ.get("ARK_BASE_URL", "https://ark-cn-beijing.bytedance.net/api/v3"),
    )
    model = os.environ.get("ARK_MODEL", "doubao-seed-2-0-code-preview-260215")

    def recall(state: LiveState) -> LiveState:
        latest = state["messages"][-1].content
        return {"openviking_context": find_tool.invoke({"query": latest, "limit": 3})}

    def answer(state: LiveState) -> LiveState:
        latest = state["messages"][-1].content
        completion = llm.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Answer in one short sentence using the supplied context. "
                        "Include the word OpenViking in the answer.\n\n"
                        f"{state.get('openviking_context', '')}"
                    ),
                },
                {"role": "user", "content": latest},
            ],
        )
        content = completion.choices[0].message.content or ""
        return {"messages": [AIMessage(content=content)]}

    graph = StateGraph(LiveState)
    graph.add_node("recall", recall)
    graph.add_node("answer", answer)
    graph.add_edge(START, "recall")
    graph.add_edge("recall", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def main() -> str:
    if os.environ.get("OPENVIKING_LANGGRAPH_LIVE") != "1":
        raise RuntimeError("Set OPENVIKING_LANGGRAPH_LIVE=1 to run the live lane.")
    if not os.environ.get("ARK_API_KEY"):
        raise RuntimeError("ARK_API_KEY is required for the live lane.")
    app = build_app()
    result = app.invoke(
        {
            "messages": [
                HumanMessage(
                    content="What does OpenViking provide to this LangGraph agent?"
                )
            ]
        }
    )
    answer = result["messages"][-1].content
    print(answer)
    if not answer.strip():
        raise RuntimeError("Live LLM returned an empty answer.")
    return answer


if __name__ == "__main__":
    main()
