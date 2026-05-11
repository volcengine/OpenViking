"""Live LangGraph app using OpenViking middleware and an OpenAI-compatible LLM.

Required:
  ARK_API_KEY

Optional:
  ARK_BASE_URL, ARK_MODEL
  OPENVIKING_URL, OPENVIKING_API_KEY, OPENVIKING_LIVE_COMMIT_TIMEOUT
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from openai import OpenAI
from typing_extensions import Annotated, TypedDict

from openviking.integrations.langchain import OpenVikingContextMiddleware
from openviking.integrations.langchain.client import extract_message_text


class LiveState(TypedDict, total=False):
    messages: Annotated[list, add_messages]


def build_context_client():
    from openviking.client import SyncHTTPClient

    client = SyncHTTPClient(
        url=os.environ.get("OPENVIKING_URL") or None,
        api_key=os.environ.get("OPENVIKING_API_KEY"),
        user_id=os.environ.get("OPENVIKING_USER_ID"),
        agent_id=os.environ.get("OPENVIKING_AGENT_ID"),
    )
    client.initialize()
    return client


def seed_context(client, session_id: str, code: str) -> None:
    client.create_session(session_id=session_id)
    client.add_message(
        session_id=session_id,
        role="user",
        parts=[
            {
                "type": "text",
                "text": (
                    f"Remember this OpenViking LangGraph live e2e exact code: {code}. "
                    "This is durable session context for the next agent turn."
                ),
            }
        ],
    )
    client.add_message(
        session_id=session_id,
        role="assistant",
        parts=[
            {
                "type": "text",
                "text": f"Stored the OpenViking LangGraph live e2e exact code: {code}.",
            }
        ],
    )


def build_app(*, client: Any, session_id: str):
    middleware = OpenVikingContextMiddleware(
        client=client,
        token_budget=8_000,
        commit_on_after_agent=False,
        include_active_messages=True,
    )

    class Runtime:
        config = {"configurable": {"thread_id": session_id}}

    def model_node(state: LiveState) -> LiveState:
        current_messages = list(state["messages"])

        class Request:
            state = {}
            runtime = Runtime()
            messages = current_messages
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
            messages: list[BaseMessage] = []
            if request.system_message is not None:
                messages.append(request.system_message)
            messages.extend(request.messages)
            answer = call_llm(messages)
            return AIMessage(content=answer)

        response = middleware.wrap_model_call(Request(), handler)
        middleware.after_agent(
            {
                "messages": current_messages + [response],
            },
            Runtime(),
        )
        return {"messages": [response]}

    graph = StateGraph(LiveState)
    graph.add_node("model", model_node)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    return graph.compile()


def call_llm(messages: list[BaseMessage]) -> str:
    openai_messages = [
        {
            "role": "system",
            "content": (
                "You are validating OpenViking as LangGraph middleware. "
                "Return only the exact lg_live_* code if one appears in the context "
                "or conversation."
            ),
        }
    ]
    for message in messages:
        content = extract_message_text(message.content)
        if isinstance(message, SystemMessage):
            openai_messages.append({"role": "system", "content": content})
        elif isinstance(message, HumanMessage):
            openai_messages.append({"role": "user", "content": content})
        elif isinstance(message, AIMessage):
            openai_messages.append({"role": "assistant", "content": content})

    llm = OpenAI(
        api_key=os.environ["ARK_API_KEY"],
        base_url=os.environ.get("ARK_BASE_URL", "https://ark-cn-beijing.bytedance.net/api/v3"),
    )
    completion = llm.chat.completions.create(
        model=os.environ.get("ARK_MODEL", "doubao-seed-2-0-code-preview-260215"),
        messages=openai_messages,
    )
    return completion.choices[0].message.content or ""


def main() -> str:
    if not os.environ.get("ARK_API_KEY"):
        raise RuntimeError("ARK_API_KEY is required for the live app.")

    client = build_context_client()
    session_id = f"langgraph-live-demo-{uuid.uuid4().hex}"
    code = f"lg_live_{uuid.uuid4().hex[:10]}"
    seed_context(client, session_id, code)
    try:
        app = build_app(client=client, session_id=session_id)
        result = app.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            "What is the OpenViking LangGraph live e2e exact code? "
                            "Answer only the exact code."
                        )
                    )
                ]
            }
        )
        answer = result["messages"][-1].content
        print(answer)
        if code not in answer.lower():
            raise RuntimeError(f"Expected {code!r} in live answer: {answer!r}")
        commit = client.commit_session(session_id)
        wait_for_commit_task(client, commit)
        return answer
    finally:
        try:
            client.delete_session(session_id)
        except Exception:
            pass


def wait_for_commit_task(client, commit: dict[str, object]) -> None:
    if commit.get("archived") is not True or not commit.get("task_id"):
        raise RuntimeError(f"OpenViking commit did not start extraction: {commit}")
    timeout = float(os.environ.get("OPENVIKING_LIVE_COMMIT_TIMEOUT", "180"))
    deadline = time.monotonic() + timeout
    last_task = None
    while time.monotonic() < deadline:
        task = client.get_task(str(commit["task_id"]))
        last_task = task
        if task and task.get("status") == "completed":
            return
        if task and task.get("status") == "failed":
            raise RuntimeError(f"OpenViking commit task failed: {task}")
        time.sleep(0.5)
    raise RuntimeError(
        f"OpenViking commit task did not complete: {commit['task_id']}; last_task={last_task}"
    )


if __name__ == "__main__":
    main()
