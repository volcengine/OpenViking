"""Native host history and capture remain independent of OV archive summaries."""

import asyncio
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("langchain_openviking")

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    convert_to_messages,
)
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda

from langchain_openviking import (
    InMemoryOpenVikingClient,
    OpenVikingCommitPolicy,
    OpenVikingContextMiddleware,
    OpenVikingSessionContextAssembler,
    with_openviking_memory,
)


class NoContextClient(InMemoryOpenVikingClient):
    def get_session_context(self, *args, **kwargs):
        raise AssertionError("native history must not request OV /context")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_host_history_survives_multiple_ov_commits(asynchronous):
    client = NoContextClient()
    history = InMemoryChatMessageHistory()
    seen = []

    def answer(messages):
        seen.append(messages)
        return AIMessage(content="response")

    async with with_openviking_memory(
        RunnableLambda(answer),
        client=client,
        history_factory=lambda _: history,
        inject_context=False,
        commit_policy=OpenVikingCommitPolicy(mode="always"),
    ) as runnable:
        config = {"configurable": {"session_id": "host"}}
        for text in ["Keep the early constraint.", "What was the constraint?"]:
            if asynchronous:
                await runnable.ainvoke([HumanMessage(content=text)], config=config)
            else:
                runnable.invoke([HumanMessage(content=text)], config=config)
    assert len(history.messages) == 4
    assert any(message.content == "Keep the early constraint." for message in seen[-1])
    assert len(client.archives["host"]) == 2


def test_recall_assembler_does_not_read_history_by_default():
    client = NoContextClient({"viking://~/memories/pref.md": "Use Rust."})
    assembler = OpenVikingSessionContextAssembler(client=client)
    assert assembler.assemble(session_id="host", query="Rust").block


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("shape", ["list", "tuple", "single", "string"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_host_history_recalls_supported_current_message_inputs(
    asynchronous, shape, streaming
):
    client = NoContextClient({"viking://resources/support.md": "support reset steps"})
    previous = HumanMessage(content="Earlier question.")
    history = InMemoryChatMessageHistory(messages=[previous])
    user = HumanMessage(content="support reset")
    current = {"list": [user], "tuple": (user,), "single": user, "string": user.content}[shape]
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "Policy.\n{openviking_context}"),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{question}"),
        ]
    )
    seen = []

    def model_input(data):
        value = data["question"]
        if isinstance(value, str):
            messages = prompt.invoke(data).to_messages()
        else:
            messages = [
                *data["history"],
                *convert_to_messages([value] if isinstance(value, BaseMessage) else value),
            ]
        seen.extend(messages)
        return messages

    async with with_openviking_memory(
        RunnableLambda(model_input) | FakeListChatModel(responses=["ok"]),
        client=client,
        history_factory=lambda _: history,
        session_id="supported-input",
        input_messages_key="question",
        history_messages_key="history",
    ) as runnable:
        payload = {"ability": "not the current query", "question": current}
        if streaming and asynchronous:
            content = "".join([chunk.content async for chunk in runnable.astream(payload)])
        elif streaming:
            content = "".join(chunk.content for chunk in runnable.stream(payload))
        else:
            response = await runnable.ainvoke(payload) if asynchronous else runnable.invoke(payload)
            content = response.content

    assert content == "ok"
    assert [call["query"] for call in client.search_calls] == ["support reset"]
    assert any("support reset steps" in str(message.content) for message in seen)
    assert seen[-1].content == "support reset"
    assert history.messages[0] is previous
    assert [message.content for message in history.messages] == [
        "Earlier question.",
        "support reset",
        "ok",
    ]
    captured = client.sessions["supported-input"]
    assert [message["role"] for message in captured] == ["user", "assistant"]
    assert captured[0]["parts"] == [{"type": "text", "text": "support reset"}]
    assert any(part["type"] == "context" for part in captured[1]["parts"])


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_capture_before_summary_and_deduplicate_retained_tail_after_restart(asynchronous):
    client = NoContextClient()
    runtime = SimpleNamespace(config={"configurable": {"thread_id": "host"}})
    state = {
        "messages": [
            HumanMessage(id="u1", content="Keep the early constraint."),
            AIMessage(id="a1", content="I will."),
            HumanMessage(id="u2", content="Now inspect the tool output."),
        ]
    }
    middleware = OpenVikingContextMiddleware(client=client, commit_on_after_agent=True)
    if asynchronous:
        update = await middleware.abefore_model(state, runtime)
    else:
        update = middleware.before_model(state, runtime)
    assert len(client.sessions["host"]) == 3
    assert not client.archives["host"], "before_model captures but does not commit"
    state.update(update)
    state["messages"] = [
        HumanMessage(
            id="summary", content="Host summary.", additional_kwargs={"lc_source": "summarization"}
        ),
        state["messages"][-1],
        AIMessage(id="a2", content="Tool output checked."),
    ]
    restarted = OpenVikingContextMiddleware(client=client, commit_on_after_agent=True)
    if asynchronous:
        state.update(await restarted.aafter_agent(state, runtime))
        await restarted.aafter_agent(state, runtime)
    else:
        state.update(restarted.after_agent(state, runtime))
        restarted.after_agent(state, runtime)
    captured = client.archives["host"][0]["messages"]
    assert len(captured) == 4
    assert not any("Host summary." in str(message) for message in captured)


def test_framework_summary_persists_and_resumes_with_no_ov_context(tmp_path):
    pytest.importorskip("langchain.agents.middleware")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    from langchain.agents import create_agent
    from langchain.agents.middleware import SummarizationMiddleware
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langgraph.checkpoint.sqlite import SqliteSaver

    client = NoContextClient()
    database = str(tmp_path / "host.sqlite")
    config = {"configurable": {"thread_id": "durable-host"}}

    def agent(checkpointer):
        return create_agent(
            FakeListChatModel(responses=["Acknowledged."]),
            middleware=[
                OpenVikingContextMiddleware(client=client, commit_on_after_agent=True),
                SummarizationMiddleware(
                    model=FakeListChatModel(
                        responses=["Early constraint: keep data in Singapore."]
                    ),
                    trigger=("messages", 4),
                    keep=("messages", 2),
                ),
            ],
            checkpointer=checkpointer,
        )

    with SqliteSaver.from_conn_string(database) as checkpointer:
        app = agent(checkpointer)
        for text in ["Keep data in Singapore.", "Inspect the deployment.", "Continue."]:
            state = app.invoke({"messages": [HumanMessage(content=text)]}, config)
        assert any(
            m.additional_kwargs.get("lc_source") == "summarization" for m in state["messages"]
        )
        assert state["openviking_captured_message_ids"]

    with SqliteSaver.from_conn_string(database) as checkpointer:
        resumed = agent(checkpointer)
        restored = resumed.get_state(config).values
        assert any("keep data in Singapore" in str(m.content) for m in restored["messages"])
        resumed.invoke({"messages": [HumanMessage(content="What is the next step?")]}, config)

    captured = [m for archive in client.archives["durable-host"] for m in archive["messages"]]
    assert len(captured) == 8  # Four user/assistant pairs, no replayed tail or synthetic summary.
    assert not any("Early constraint:" in str(message) for message in captured)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("inject_context", [False, True])
@pytest.mark.parametrize("shape", ["list", "tuple", "dict", "dict_tuple"])
@pytest.mark.asyncio
async def test_recall_preserves_existing_system_message(
    asynchronous, structured, inject_context, shape
):
    content = "Keep the deployment policy."
    if structured:
        content = [
            {
                "type": "text",
                "text": "Keep the deployment policy.",
                "cache_control": {"type": "ephemeral"},
            }
        ]
    system = SystemMessage(
        content=content,
        id="policy-id",
        name="policy",
        additional_kwargs={"host_field": {"keep": True}},
        response_metadata={"source": "host"},
    )
    before = deepcopy(system.model_dump())
    client = InMemoryOpenVikingClient({"viking://resources/policy.md": "Deployment uses azure."})
    seen = []

    def answer(messages):
        if isinstance(messages, dict):
            messages = messages["messages"]
        seen.extend(messages)
        return AIMessage(content="Acknowledged.")

    async with with_openviking_memory(
        RunnableLambda(answer),
        client=client,
        history_factory=lambda _: InMemoryChatMessageHistory(),
        inject_context=inject_context,
    ) as runnable:
        values = [system, HumanMessage(content="azure deployment")]
        if shape == "tuple":
            values = tuple(values)
        elif shape == "dict":
            values = {"messages": values}
        elif shape == "dict_tuple":
            values = {"messages": tuple(values)}
        original_values = deepcopy(values)
        config = {"configurable": {"session_id": "policy"}}
        if asynchronous:
            await runnable.ainvoke(values, config=config)
        else:
            runnable.invoke(values, config=config)

    observed = next(message for message in seen if isinstance(message, SystemMessage))
    assert observed.model_dump(exclude={"content"}) == system.model_dump(exclude={"content"})
    assert system.model_dump() == before
    assert values == original_values
    if not inject_context:
        assert observed.content == content
    elif structured:
        assert observed.content[:-1] == content
        assert observed.content[-1]["type"] == "text"
        assert "Deployment uses azure." in observed.content[-1]["text"]
    else:
        assert observed.content.startswith(f"{content}\n\n<openviking_context>")
        assert "Deployment uses azure." in observed.content


@pytest.mark.parametrize("capture_mode", ["sync", "async-sync-client", "async-native-client"])
@pytest.mark.parametrize("first_commits", [False, True])
@pytest.mark.asyncio
async def test_concurrent_capture_hooks_keep_their_commit_policies(capture_mode, first_commits):
    entered = threading.Event()
    release = threading.Event()

    class PausedClient(NoContextClient):
        def batch_add_messages(self, session_id, messages, **kwargs):
            if session_id == "first":
                entered.set()
                assert release.wait(5), "other session did not finish its capture"
            return super().batch_add_messages(session_id, messages, **kwargs)

    class AsyncPausedClient(NoContextClient):
        async def batch_add_messages(self, session_id, messages, **kwargs):
            if session_id == "first":
                entered.set()
                assert await asyncio.to_thread(release.wait, 5), "other session did not finish"
            return super().batch_add_messages(session_id, messages, **kwargs)

    client = AsyncPausedClient() if capture_mode == "async-native-client" else PausedClient()
    client_kwargs = {"async_client" if capture_mode == "async-native-client" else "client": client}
    middleware = OpenVikingContextMiddleware(**client_kwargs, commit_on_after_agent=True)

    async def capture(session_id, commit):
        name = "after_agent" if commit else "before_model"
        state = {"session_id": session_id, "messages": [HumanMessage(content=session_id)]}
        if capture_mode != "sync":
            await getattr(middleware, "a" + name)(state, None)
        else:
            await asyncio.to_thread(getattr(middleware, name), state, None)

    first = asyncio.create_task(capture("first", first_commits))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await capture("second", not first_commits)
    finally:
        release.set()
        await first
        await middleware.aclose()

    committed, pending = ("first", "second") if first_commits else ("second", "first")
    assert len(client.archives[committed]) == 1
    assert client.archives[committed][0]["messages"][0]["parts"][0]["text"] == committed
    assert not client.sessions[committed]
    assert not client.archives[pending]
    assert client.sessions[pending][0]["parts"][0]["text"] == pending
