from __future__ import annotations

import os
import time
import uuid
from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("openai")

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from openai import OpenAI
from typing_extensions import Annotated, TypedDict

from openviking.integrations.langchain import (
    OpenVikingContextMiddleware,
    create_openviking_tools,
    with_openviking_context,
)
from openviking.integrations.langchain.client import extract_message_text


def test_true_live_langchain_context_backend_e2e():
    _require_live_env()
    client = _build_real_client()
    session_id = f"langchain-live-e2e-{uuid.uuid4().hex}"
    code = f"lc_live_{uuid.uuid4().hex[:10]}"

    try:
        _seed_session_context(
            client,
            session_id,
            code,
            framework="LangChain",
        )
        app = with_openviking_context(
            RunnableLambda(_langchain_live_model),
            client=client,
            session_id=session_id,
            token_budget=8_000,
        )

        first = app.invoke(
            [
                HumanMessage(
                    content=(
                        "What is the OpenViking LangChain live e2e exact code? "
                        "Answer only the exact code."
                    )
                )
            ]
        )
        assert code in first.content.lower()

        second = app.invoke(
            [
                HumanMessage(
                    content=(
                        "Repeat the exact code from the previous answer. "
                        "Answer only the exact code."
                    )
                )
            ]
        )
        assert code in second.content.lower()

        context = client.get_session_context(session_id, token_budget=8_000)
        assert len(context["messages"]) >= 4
        assert code in str(context).lower()

        commit = client.commit_session(session_id, keep_recent_count=0)
        _wait_for_commit_task(client, commit)
        archive_id = _archive_id_from_commit(commit)
        assert archive_id

        tools = {tool.name: tool for tool in create_openviking_tools(client=client)}
        archive_search = _invoke_until_contains(
            lambda: tools["viking_archive_search"].invoke(
                {"session_id": session_id, "query": code}
            ),
            code,
            label="archive search",
        )
        archive_expand = _invoke_until_contains(
            lambda: tools["viking_archive_expand"].invoke(
                {"session_id": session_id, "archive_id": archive_id}
            ),
            code,
            label="archive expand",
        )

        recovered = _call_llm(
            [
                {
                    "role": "system",
                    "content": "Return only the exact code present in the archive text.",
                },
                {
                    "role": "user",
                    "content": f"Archive search:\n{archive_search}\n\nArchive:\n{archive_expand}",
                },
            ]
        )
        assert code in recovered.lower()
    finally:
        _cleanup(client, session_id)


def test_true_live_langgraph_middleware_e2e():
    _require_live_env()
    client = _build_real_client()
    session_id = f"langgraph-live-e2e-{uuid.uuid4().hex}"
    code = f"lg_live_{uuid.uuid4().hex[:10]}"

    try:
        _seed_session_context(
            client,
            session_id,
            code,
            framework="LangGraph",
        )
        app = _build_langgraph_live_app(
            client=client,
            session_id=session_id,
        )

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
        assert code in answer.lower()

        context = client.get_session_context(session_id, token_budget=8_000)
        assert [message["role"] for message in context["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert code in str(context).lower()

        commit = client.commit_session(session_id, keep_recent_count=0)
        _wait_for_commit_task(client, commit)
        archive_id = _archive_id_from_commit(commit)
        assert archive_id

        tools = {tool.name: tool for tool in create_openviking_tools(client=client)}
        archive_expand = _invoke_until_contains(
            lambda: tools["viking_archive_expand"].invoke(
                {"session_id": session_id, "archive_id": archive_id}
            ),
            code,
            label="archive expand",
        )
        recovered = _call_llm(
            [
                {
                    "role": "system",
                    "content": "Return only the exact code present in the archive text.",
                },
                {"role": "user", "content": archive_expand},
            ]
        )
        assert code in recovered.lower()
    finally:
        _cleanup(client, session_id)


def _langchain_live_model(messages: list[BaseMessage]) -> AIMessage:
    answer = _call_llm(
        _langchain_messages_to_openai(
            messages,
            instruction=(
                "You are validating OpenViking as a LangChain context backend. "
                "Return only the exact lc_live_* code if one appears in the context "
                "or conversation."
            ),
        )
    )
    return AIMessage(content=answer)


class _LiveGraphState(TypedDict, total=False):
    messages: Annotated[list, add_messages]


def _build_langgraph_live_app(*, client: Any, session_id: str):
    middleware = OpenVikingContextMiddleware(
        client=client,
        token_budget=8_000,
        commit_on_after_agent=False,
        include_active_messages=True,
    )

    class Runtime:
        config = {"configurable": {"thread_id": session_id}}

    def model_node(state: _LiveGraphState) -> _LiveGraphState:
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
            answer = _call_llm(
                _langchain_messages_to_openai(
                    messages,
                    instruction=(
                        "You are validating OpenViking as LangGraph middleware. "
                        "Return only the exact lg_live_* code if one appears in the "
                        "context or conversation."
                    ),
                )
            )
            return AIMessage(content=answer)

        response = middleware.wrap_model_call(Request(), handler)
        all_messages = current_messages + [response]
        middleware.after_agent(
            {
                "messages": all_messages,
            },
            Runtime(),
        )
        return {"messages": [response]}

    graph = StateGraph(_LiveGraphState)
    graph.add_node("model", model_node)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    return graph.compile()


def _langchain_messages_to_openai(
    messages: list[BaseMessage],
    *,
    instruction: str,
) -> list[dict[str, str]]:
    converted = [{"role": "system", "content": instruction}]
    for message in messages:
        content = extract_message_text(message.content)
        if isinstance(message, SystemMessage):
            converted.append({"role": "system", "content": content})
        elif isinstance(message, HumanMessage):
            converted.append({"role": "user", "content": content})
        elif isinstance(message, AIMessage):
            converted.append({"role": "assistant", "content": content})
    return converted


def _call_llm(messages: list[dict[str, str]]) -> str:
    client = OpenAI(
        api_key=os.environ["ARK_API_KEY"],
        base_url=os.environ.get("ARK_BASE_URL", "https://ark-cn-beijing.bytedance.net/api/v3"),
    )
    completion = client.chat.completions.create(
        model=os.environ.get("ARK_MODEL", "doubao-seed-2-0-code-preview-260215"),
        messages=messages,
    )
    return completion.choices[0].message.content or ""


def _require_live_env() -> None:
    assert os.environ.get("ARK_API_KEY"), "ARK_API_KEY is required for live e2e"


def _build_real_client():
    from openviking.client import SyncHTTPClient

    client = SyncHTTPClient(
        url=os.environ.get("OPENVIKING_URL") or None,
        api_key=os.environ.get("OPENVIKING_API_KEY"),
        user_id=os.environ.get("OPENVIKING_USER_ID"),
        agent_id=os.environ.get("OPENVIKING_AGENT_ID"),
    )
    client.initialize()
    return client


def _seed_session_context(client, session_id: str, code: str, *, framework: str) -> None:
    client.create_session(session_id=session_id)
    client.add_message(
        session_id=session_id,
        role="user",
        parts=[
            {
                "type": "text",
                "text": (
                    f"Remember this OpenViking {framework} live e2e exact code: {code}. "
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
                "text": f"Stored the OpenViking {framework} live e2e exact code: {code}.",
            }
        ],
    )


def _cleanup(client, session_id: str) -> None:
    try:
        client.delete_session(session_id)
    except Exception:
        pass


def _archive_id_from_commit(commit: dict[str, object]) -> str | None:
    archive_id = commit.get("archive_id")
    if archive_id:
        return str(archive_id)
    archive_uri = str(commit.get("archive_uri") or "").rstrip("/")
    if not archive_uri:
        return None
    return archive_uri.rsplit("/", 1)[-1]


def _invoke_until_contains(
    invoke,
    expected: str,
    *,
    label: str,
) -> str:
    timeout = float(os.environ.get("OPENVIKING_LIVE_ARCHIVE_TIMEOUT", "60"))
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    last_value = ""
    while time.monotonic() < deadline:
        try:
            value = str(invoke())
        except Exception as exc:
            last_error = exc
        else:
            last_value = value
            if expected in value.lower():
                return value
        time.sleep(0.5)
    pytest.fail(
        f"OpenViking {label} did not contain {expected!r}; "
        f"last_error={last_error!r}; last_value={last_value[:1000]!r}"
    )


def _wait_for_commit_task(client, commit: dict[str, object]) -> None:
    assert commit.get("archived") is True
    task_id = commit.get("task_id")
    assert task_id, f"OpenViking commit did not start extraction: {commit}"
    timeout = float(os.environ.get("OPENVIKING_LIVE_COMMIT_TIMEOUT", "180"))
    deadline = time.monotonic() + timeout
    last_task = None
    while time.monotonic() < deadline:
        task = client.get_task(str(task_id))
        last_task = task
        if task and task.get("status") == "completed":
            return
        if task and task.get("status") == "failed":
            pytest.fail(f"OpenViking commit task failed: {task}")
        time.sleep(0.5)
    pytest.fail(f"OpenViking commit task did not complete: {task_id}; last_task={last_task}")
