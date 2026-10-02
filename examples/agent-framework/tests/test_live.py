# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Opt-in contracts using a real server and model. No model responses are mocked."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import AsyncExitStack
from uuid import uuid4

import httpx
import pytest
from agent_framework import Agent, AgentLoopMiddleware, AgentSession, Message
from agent_framework.openai import OpenAIChatCompletionClient
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openviking_sdk import AsyncHTTPClient

from openviking_agent_framework import OpenVikingContextProvider

if os.getenv("OPENVIKING_TEST_ENV"):
    load_dotenv(os.environ["OPENVIKING_TEST_ENV"], override=False)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("OPENVIKING_TEST_ROOT_KEY"),
        reason="requires a dedicated OpenViking server and real model credentials",
    ),
]


@pytest.fixture
def faults():
    return {"request": [], "response": []}


@pytest.fixture
async def clients(faults):
    """Create two ordinary users; remove the test account on completion."""
    url = os.environ["OPENVIKING_TEST_URL"]
    root = AsyncHTTPClient(url=url, api_key=os.environ["OPENVIKING_TEST_ROOT_KEY"], timeout=180)
    await root.initialize()
    account = "maf-test-" + uuid4().hex
    opened = []

    async def on_request(request):
        for callback in faults["request"]:
            await callback(request)

    async def on_response(response):
        for callback in faults["response"]:
            await callback(response)

    await root.admin_create_account(account, "admin")
    try:
        for user in ("alice", "bob"):
            result = await root.admin_register_user(account, user)
            client = AsyncHTTPClient(
                url=url,
                api_key=result["user_key"],
                timeout=180,
                event_hooks={"request": [on_request], "response": [on_response]},
            )
            await client.initialize()
            opened.append(client)
        yield opened
    finally:
        for client in opened:
            await client.close()
        await root.admin_delete_account(account)
        await root.close()


@pytest.fixture
async def model():
    """Observe actual HTTP model inputs without changing responses."""
    requests = []

    async def observe(request):
        requests.append(json.loads(request.content))

    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(
            httpx.AsyncClient(event_hooks={"request": [observe]}, timeout=180)
        )
        api = await stack.enter_async_context(
            AsyncOpenAI(
                api_key=os.environ["OPENAI_API_KEY"],
                base_url=os.environ["OPENAI_BASE_URL"],
                http_client=http,
                max_retries=0,
            )
        )
        yield (
            OpenAIChatCompletionClient(model=os.environ["OPENAI_MODEL"], async_client=api),
            requests,
        )


def make_agent(chat, provider=None, **kwargs):
    return Agent(
        chat,
        instructions=(
            "Use supplied reference data when relevant. If a fact is unknown, say UNKNOWN."
        ),
        context_providers=[provider] if provider else [],
        **kwargs,
    )


async def records(client, session, provider):
    session_id = session.state[provider.source_id]["session_id"]
    text = await client.read(f"viking://~/sessions/{session_id}/messages.jsonl")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def wait_task(client, task_id):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        task = await client.get_task(task_id)
        assert task is not None
        if task["status"] == "completed":
            return task
        assert task["status"] not in ("failed", "cancelled"), task
        await asyncio.sleep(1)
    pytest.fail("memory extraction did not complete within 240 seconds")


async def test_memory_reaches_new_agent_and_stays_private(clients, model):
    chat, requests = model
    alice, bob = clients
    provider = OpenVikingContextProvider(alice)
    agent = make_agent(chat, provider)
    session = agent.create_session()
    label = "LANTERN-" + uuid4().hex[:16]
    await agent.run(
        f"My preferred project label for the MAF integration is {label}. "
        "Remember this label for future conversations. Reply briefly.",
        session=session,
    )
    stored = await records(alice, session, provider)
    assert label in json.dumps(stored)
    committed = await provider.commit(session)
    await wait_task(alice, committed["task_id"])
    question = (
        "What is my preferred project label for the MAF integration? Reply only with the label."
    )
    recalled = await alice.search_context(query=question, options={"max_tokens": 4000})
    sources = [entry["uri"] for entry in recalled["entries"] if label in entry["text"]]
    assert sources, recalled
    assert any([label in await alice.read(uri) for uri in sources])

    # A new agent and session have no old chat history.
    restored_provider = OpenVikingContextProvider(alice)
    fresh = make_agent(chat, restored_provider)
    requests.clear()
    fresh_session = fresh.create_session()
    answer = await fresh.run(question, session=fresh_session)
    assert label in answer.text
    wire = json.dumps(requests)
    assert label in wire and any(uri in wire for uri in sources)
    assert not any(
        "Retrieved OpenViking context" in json.dumps(message)
        for message in await records(alice, fresh_session, restored_provider)
    )

    # Both controls make real model calls with no access to Alice's fact.
    requests.clear()
    other = make_agent(chat, OpenVikingContextProvider(bob))
    disabled = make_agent(chat)
    controls = await asyncio.gather(
        other.run(question, session=other.create_session()),
        disabled.run(question, session=disabled.create_session()),
    )
    assert label not in json.dumps(requests)
    assert all(label not in response.text for response in controls)
    assert label not in json.dumps(await bob.search_context(query=question))

    # Concurrent runs use separate authenticated clients and provider state.
    markers = ["ALICE-" + uuid4().hex, "BOB-" + uuid4().hex]
    providers = [OpenVikingContextProvider(alice), OpenVikingContextProvider(bob)]
    agents = [make_agent(chat, provider) for provider in providers]
    sessions = [agent.create_session() for agent in agents]
    await asyncio.gather(
        *(
            agent.run(f"My private audit marker is {marker}. Only reply OK.", session=current)
            for agent, marker, current in zip(agents, markers, sessions, strict=True)
        )
    )
    for index, (client, current, provider) in enumerate(
        zip(clients, sessions, providers, strict=True)
    ):
        captured = json.dumps(await records(client, current, provider))
        assert markers[index] in captured
        assert markers[1 - index] not in captured


async def test_streaming_tools_repeated_text_and_restoration(clients, model):
    chat, _ = model
    provider = OpenVikingContextProvider(clients[0])
    value = "TOOL-" + uuid4().hex

    def lookup_label() -> str:
        """Read the current project label."""
        return value

    agent = make_agent(chat, provider, tools=[lookup_label])
    session = agent.create_session()
    async with agent.run(
        "Call lookup_label and return its result.", session=session, stream=True
    ) as stream:
        async for _ in stream:
            pass
        response = await stream.get_final_response()
    assert value in response.text
    stored = await records(clients[0], session, provider)
    tools = [part for message in stored for part in message["parts"] if part["type"] == "tool"]
    calls = [part for part in tools if part["tool_status"] == "pending"]
    results = [part for part in tools if part["tool_status"] == "completed"]
    assert len(calls) == len(results) == 1
    assert calls[0]["tool_id"] == results[0]["tool_id"]
    assert calls[0]["tool_name"] == results[0]["tool_name"] == "lookup_label"
    assert value in results[0]["tool_output"]

    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    for _ in range(2):
        await agent.run("Say HELLO without using tools.", session=restored)
    all_records = await records(clients[0], restored, provider)
    repeated = [
        m
        for m in all_records
        if m["role"] == "user" and "Say HELLO without using tools." in json.dumps(m["parts"])
    ]
    assert len(repeated) == 2
    ids = [m["source_message_ids"][0] for m in all_records]
    assert len(ids) == len(set(ids))
    assert len(all_records) == len(stored) + 4

    previous_user = Message(
        "user",
        ["Call lookup_label and return its result."],
        message_id=stored[0]["source_message_ids"][0],
    )
    await agent.run(
        [previous_user, *response.messages, Message("user", ["Say BYE without using tools."])],
        session=restored,
    )
    assert len(await records(clients[0], restored, provider)) == len(stored) + 6


async def test_internal_loop_captures_each_message_once(clients, model):
    chat, requests = model
    provider = OpenVikingContextProvider(clients[0])
    loop = AgentLoopMiddleware(
        should_continue=lambda iteration, **_: iteration < 2,
        max_iterations=2,
        next_message=lambda **_: "Now say SECOND.",
        inject_progress=False,
    )
    agent = make_agent(chat, provider, middleware=[loop])
    session = agent.create_session()
    await agent.run("Say FIRST.", session=session)
    assert len(requests) == 2
    stored = await records(clients[0], session, provider)
    assert len([m for m in stored if m["role"] == "assistant"]) == 2
    assert len([m for m in stored if "Say FIRST." in json.dumps(m["parts"])]) == 1
    ids = [m["source_message_ids"][0] for m in stored]
    assert len(ids) == len(set(ids))


async def test_partial_batch_failure_retains_only_unsent_tail(clients, model, faults):
    chat, _ = model
    client = clients[0]
    provider = OpenVikingContextProvider(client)
    agent = make_agent(chat, provider)
    session = agent.create_session()
    batches = 0

    async def fail_second_batch(request):
        nonlocal batches
        if request.url.path.endswith("/messages/batch"):
            batches += 1
            if batches == 2:
                raise httpx.ConnectError("injected before send", request=request)

    faults["request"].append(fail_second_batch)
    inputs = [Message("user", [f"Item {i}."], message_id=f"item-{i}") for i in range(101)]
    inputs.append(Message("user", ["Acknowledge the list briefly."], message_id="last"))
    with pytest.raises(Exception, match="injected before send"):
        await agent.run(inputs, session=session)
    state = session.state[provider.source_id]
    assert len(state["recorded_message_ids"]) == 100
    assert len(state["pending_messages"]) == 3
    assert len(await records(client, session, provider)) == 100
    with pytest.raises(RuntimeError, match="pending"):
        await provider.commit(session)
    with pytest.raises(RuntimeError, match="unconfirmed"):
        await agent.run("Do not run yet.", session=session)
    faults["request"].remove(fail_second_batch)
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    await provider.flush(restored)
    stored = await records(client, restored, provider)
    assert len(stored) == 103
    assert len({m["source_message_ids"][0] for m in stored}) == 103
    assert not restored.state[provider.source_id]["pending_messages"]


@pytest.mark.parametrize("cancel", [False, True])
async def test_lost_acknowledgement_never_replays_implicitly(clients, model, faults, cancel):
    chat, _ = model
    client = clients[0]
    provider = OpenVikingContextProvider(client)
    agent = make_agent(chat, provider)
    session = agent.create_session()

    async def lose_ack(response):
        if response.request.url.path.endswith("/messages/batch"):
            await response.aread()
            assert response.status_code == 200
            if cancel:
                raise asyncio.CancelledError()
            raise httpx.ReadError("injected lost acknowledgement", request=response.request)

    faults["response"].append(lose_ack)
    expected = asyncio.CancelledError if cancel else Exception
    with pytest.raises(expected):
        await agent.run("Say ACK.", session=session)
    faults["response"].remove(lose_ack)
    state = session.state[provider.source_id]
    assert len(state["pending_messages"]) == 2
    assert not state.get("recorded_message_ids")
    assert len(await records(client, session, provider)) == 2
    with pytest.raises(RuntimeError, match="pending"):
        await provider.commit(session)
    with pytest.raises(RuntimeError, match="unconfirmed"):
        await agent.run("Say AGAIN.", session=session)
    assert len(await records(client, session, provider)) == 2

    # Caller reconciles a confirmed append; blindly calling flush would duplicate it.
    stored_ids = {m["source_message_ids"][0] for m in await records(client, session, provider)}
    assert stored_ids == {m["source_message_ids"][0] for m in state["pending_messages"]}
    state["recorded_message_ids"] = list(stored_ids)
    state["pending_messages"] = []
    await agent.run("Say RESUMED.", session=session)
    assert len(await records(client, session, provider)) == 4


async def test_incomplete_stream_is_not_reported_as_captured(clients, model):
    chat, _ = model
    provider = OpenVikingContextProvider(clients[0])
    agent = make_agent(chat, provider)
    session = agent.create_session()
    stream = agent.run("Count from one to one hundred.", session=session, stream=True)
    async for update in stream:
        if update.text:
            break
    await stream.close()
    state = session.state[provider.source_id]
    assert not state.get("recorded_message_ids")
    assert not state.get("pending_messages")
    assert await records(clients[0], session, provider) == []


async def test_recall_error_stops_inference_and_can_be_retried(clients, model, faults):
    chat, requests = model
    provider = OpenVikingContextProvider(clients[0])
    agent = make_agent(chat, provider)
    session = agent.create_session()

    async def unavailable(request):
        if request.url.path.endswith("/search/search"):
            raise httpx.ConnectError("injected unavailable server", request=request)

    faults["request"].append(unavailable)
    with pytest.raises(Exception, match="injected unavailable server"):
        await agent.run("Say HELLO.", session=session)
    assert not requests
    assert await records(clients[0], session, provider) == []
    faults["request"].clear()
    await agent.run("Say HELLO.", session=session)
    assert len(await records(clients[0], session, provider)) == 2


async def test_invalid_server_credentials_stop_before_inference(model):
    chat, requests = model
    client = AsyncHTTPClient(
        url=os.environ["OPENVIKING_TEST_URL"], api_key="invalid-" + uuid4().hex, timeout=10
    )
    await client.initialize()
    try:
        agent = make_agent(chat, OpenVikingContextProvider(client))
        with pytest.raises(Exception, match="(?i)(auth|key)"):
            await agent.run("Say HELLO.", session=agent.create_session())
        assert not requests
    finally:
        await client.close()


async def test_commit_error_retains_session_for_task_inspection(clients, model, faults):
    chat, _ = model
    client = clients[0]
    provider = OpenVikingContextProvider(client)
    agent = make_agent(chat, provider)
    session = agent.create_session()
    await agent.run("Remember that I prefer concise answers.", session=session)
    saved = json.loads(json.dumps(session.state[provider.source_id]))
    jobs = []

    async def lose_commit_ack(response):
        if response.request.url.path.endswith("/commit"):
            await response.aread()
            assert response.status_code == 200
            jobs.append(response.json()["result"])
            raise httpx.ReadError("injected lost commit acknowledgement", request=response.request)

    faults["response"].append(lose_commit_ack)
    with pytest.raises(Exception, match="injected lost commit acknowledgement"):
        await provider.commit(session)
    faults["response"].clear()
    assert session.state[provider.source_id] == saved
    assert len(jobs) == 1
    await wait_task(client, jobs[0]["task_id"])
