"""Native host history and capture remain independent of OV archive summaries."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")
pytest.importorskip("langchain_openviking")

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.messages import AIMessage, HumanMessage
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
@pytest.mark.parametrize("first_commits", [False, True])
@pytest.mark.asyncio
async def test_concurrent_capture_hooks_keep_their_commit_policies(asynchronous, first_commits):
    entered = threading.Event()
    release = threading.Event()

    class PausedClient(NoContextClient):
        def batch_add_messages(self, session_id, messages, **kwargs):
            if session_id == "first":
                entered.set()
                assert release.wait(5), "other session did not finish its capture"
            return super().batch_add_messages(session_id, messages, **kwargs)

    client = PausedClient()
    middleware = OpenVikingContextMiddleware(client=client, commit_on_after_agent=True)

    async def capture(session_id, commit):
        name = "after_agent" if commit else "before_model"
        state = {"session_id": session_id, "messages": [HumanMessage(content=session_id)]}
        if asynchronous:
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
