"""Peer scoping: X-OpenViking-Actor-Peer and a gateway key's default peer."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking_gateway.storage import digest
from openviking_gateway.tool_executor import ToolExecutor
from openviking_gateway.tool_protocols import ResponseCapture

PEER = "X-OpenViking-Actor-Peer"


def observe(app):
    """Record the headers of the gateway's OpenViking calls, keyed by path kind."""
    calls = {"search": [], "search_body": [], "write": [], "mcp": []}

    async def handler(request):
        if request.path == "/api/v1/search/search":
            calls["search"].append(dict(request.headers))
            calls["search_body"].append(await request.json())
        elif request.path.endswith("/messages/batch"):
            calls["write"].append(dict(request.headers))
        return None

    app.state.test_backend["handler"] = handler
    return calls


async def issue(client, admin, **fields):
    response = await client.post(
        "/admin/keys",
        headers=admin,
        json={
            "name": "peer",
            "openviking_key": "user-key",
            "policy_id": "default",
            "upstream_ids": ["chat"],
            **fields,
        },
    )
    return response


async def two_turns(client, key, headers=None):
    """A conversation of two user turns; the second confirms the first for capture."""
    headers = {"Authorization": "Bearer " + key, "x-openviking-session": "s1", **(headers or {})}
    first = [{"role": "user", "content": "How do we deploy?"}]
    response = await client.post(
        "/v1/chat/completions", headers=headers, json={"model": "model", "messages": first}
    )
    assert response.status_code == 200, response.text
    second = [
        *first,
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "And roll back?"},
    ]
    response = await client.post(
        "/v1/chat/completions", headers=headers, json={"model": "model", "messages": second}
    )
    assert response.status_code == 200, response.text


async def delivered(app, writes):
    for _ in range(20):
        await app.state.worker.once()
        if writes:
            break
        await asyncio.sleep(0.01)
    return [message for batch in writes for message in batch["messages"]]


def header(headers):
    return {k.lower(): v for k, v in headers.items()}.get(PEER.lower())


async def test_header_reaches_recall_and_names_the_captured_user(running_gateway):
    app, client, admin, key, seen, writes = running_gateway
    calls = observe(app)
    await two_turns(client, key["key"], {PEER: " review-agent "})
    assert calls["search"] and all(header(h) == "review-agent" for h in calls["search"])
    # Like the memory plugins by default, the peer goes only in the header, so the
    # server's default peer_scope "all" still returns other peers' memories, with a penalty.
    assert all("peer_scope" not in body for body in calls["search_body"])
    # The model upstream never sees the header.
    assert seen and all(header(h) is None for _, _, h in seen)
    messages = await delivered(app, writes)
    users = [m for m in messages if m["role"] == "user"]
    assert users and all(m["peer_id"] == "review-agent" for m in users)
    assert all("peer_id" not in m for m in messages if m["role"] != "user")
    # Session writes carry the peer in the body; OpenViking ignores an actor peer there.
    assert calls["write"] and all(header(h) is None for h in calls["write"])
    logs = (await client.get("/admin/logs", headers=admin)).json()
    assert {log.get("peer") for log in logs if log.get("kind") != "capture"} == {"review-agent"}


async def test_key_default_peer_applies_without_a_header(running_gateway):
    app, client, admin, _, _, writes = running_gateway
    response = await issue(client, admin, default_peer_id="bot-a")
    assert response.status_code == 200, response.text
    issued = response.json()
    assert issued["default_peer_id"] == "bot-a"
    listed = (await client.get("/admin/keys/" + issued["id"], headers=admin)).json()
    assert listed["default_peer_id"] == "bot-a"
    calls = observe(app)
    await two_turns(client, issued["key"])
    assert calls["search"] and all(header(h) == "bot-a" for h in calls["search"])
    users = [m for m in await delivered(app, writes) if m["role"] == "user"]
    assert users and all(m["peer_id"] == "bot-a" for m in users)


@pytest.mark.parametrize("sent", ["bot-b", "   "])
async def test_header_overrides_the_default_and_blank_keeps_it(running_gateway, sent):
    app, client, admin, _, _, writes = running_gateway
    issued = (await issue(client, admin, default_peer_id="bot-a")).json()
    calls = observe(app)
    await two_turns(client, issued["key"], {PEER: sent})
    expected = sent.strip() or "bot-a"
    assert calls["search"] and all(header(h) == expected for h in calls["search"])
    users = [m for m in await delivered(app, writes) if m["role"] == "user"]
    assert users and all(m["peer_id"] == expected for m in users)


@pytest.mark.parametrize("value", ["bad/peer", "a@b@c", "..", "two words"])
async def test_invalid_peer_is_rejected_before_any_call(running_gateway, value):
    app, client, admin, key, seen, _ = running_gateway
    calls = observe(app)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + key["key"], PEER: value},
        json={"model": "model", "messages": [{"role": "user", "content": "How do we deploy?"}]},
    )
    assert response.status_code == 400
    assert response.json()["reason"] == "peer_invalid"
    assert value not in response.json()["detail"]
    assert not seen and not calls["search"]
    response = await issue(client, admin, default_peer_id=value)
    assert response.status_code == 422
    assert response.json()["reason"] == "invalid_configuration"


async def test_no_peer_leaves_requests_unchanged(running_gateway):
    app, client, admin, key, _, writes = running_gateway
    calls = observe(app)
    await two_turns(client, key["key"])
    assert calls["search"] and all(header(h) is None for h in calls["search"])
    messages = await delivered(app, writes)
    assert messages and all("peer_id" not in m for m in messages)
    logs = (await client.get("/admin/logs", headers=admin)).json()
    requests = [log for log in logs if log.get("kind") != "capture"]
    assert all("peer" not in log for log in requests)
    # The session is the header's digest, as before peers.
    assert {log["session"] for log in requests} == {digest("s1")}


async def chat(client, key, messages, peer=None, session="shared"):
    headers = {"Authorization": "Bearer " + key, **({PEER: peer} if peer else {})}
    if session:
        headers["x-openviking-session"] = session
    response = await client.post(
        "/v1/chat/completions", headers=headers, json={"model": "model", "messages": messages}
    )
    assert response.status_code == 200, response.text


def user(text):
    return {"role": "user", "content": text}


REPLY = {"role": "assistant", "content": "hello"}


async def test_one_session_holds_user_messages_from_several_peers(running_gateway):
    """A group chat: one session header, a different peer for each user message."""
    app, client, admin, key, _, writes = running_gateway
    calls = observe(app)
    history = []
    turns = [("I am Alice", "alice-bot"), ("I am Bob", "bob-bot")]
    for text, peer in [*turns, ("Anyone?", None), ("Carol here", "c")]:
        history = [*history, *([REPLY] if history else []), user(text)]
        await chat(client, key["key"], history, peer)
    # Each request recalls with its own peer.
    assert [header(h) for h in calls["search"]] == ["alice-bot", "bob-bot", None, "c"]
    logs = (await client.get("/admin/logs", headers=admin)).json()
    requests = [log for log in logs if log.get("kind") != "capture"]
    # Session IDs are the header's digest whatever the peer.
    assert {log["session"] for log in requests} == {digest("shared")}
    messages = await delivered(app, writes)
    peers = {m["parts"][0]["text"]: m.get("peer_id") for m in messages if m["role"] == "user"}
    assert peers["I am Alice"] == "alice-bot" and peers["I am Bob"] == "bob-bot"
    assert "Anyone?" in peers and peers["Anyone?"] is None
    assert all("peer_id" not in m for m in messages if m["role"] != "user")
    # Capture reset takes the session alone.
    path = "/admin/keys/" + requests[0]["credential_id"] + "/capture/reset"
    body = {"session": "shared", "protocol": "chat"}
    assert (await client.post(path, headers=admin, json=body)).status_code == 200


async def test_a_message_keeps_the_peer_of_the_request_that_first_sent_it(running_gateway):
    app, client, _, key, _, writes = running_gateway
    # History the gateway sees for the first time takes the peer of that request.
    first = [user("Earlier, unseen"), REPLY, user("I am Alice")]
    await chat(client, key["key"], first, "alice-bot")
    # A peerless message stays peerless when a peer's request carries it later.
    await chat(client, key["key"], [*first, REPLY, user("No peer here")])
    await chat(client, key["key"], [*first, REPLY, user("No peer here"), REPLY, user("Bob")], "bob")
    messages = await delivered(app, writes)
    peers = [(m["parts"][0]["text"], m.get("peer_id")) for m in messages if m["role"] == "user"]
    assert peers[:3] == [
        ("Earlier, unseen", "alice-bot"),
        ("I am Alice", "alice-bot"),
        ("No peer here", None),
    ]


async def test_a_peerless_first_turn_stays_peerless(running_gateway):
    app, client, _, key, _, writes = running_gateway
    await chat(client, key["key"], [user("No peer here")])
    await chat(client, key["key"], [user("No peer here"), REPLY, user("I am Bob")], "bob")
    later = [user("No peer here"), REPLY, user("I am Bob"), REPLY, user("Carol")]
    await chat(client, key["key"], later, "carol")
    messages = await delivered(app, writes)
    peers = [(m["parts"][0]["text"], m.get("peer_id")) for m in messages if m["role"] == "user"]
    assert peers[:2] == [("No peer here", None), ("I am Bob", "bob")]


async def group_chat(client, key):
    """Alice, Bob and Carol each send a turn of one shared session."""
    history = []
    for text, peer in (("I am Alice", "alice"), ("I am Bob", "bob"), ("I am Carol", "carol")):
        history = [*history, *([REPLY] if history else []), user(text)]
        await chat(client, key, history, peer)
    return history


@pytest.mark.parametrize("change", ["edit_before_bob", "sliding_window"])
async def test_a_changed_prefix_keeps_later_messages_peers(running_gateway, change):
    """A new OpenViking session after an edit or a dropped exchange keeps every peer."""
    app, client, _, key, _, writes = running_gateway
    sessions = []

    async def handler(request):
        if request.path.endswith("/messages/batch"):
            sessions.append(request.path.split("/")[4])

    app.state.test_backend["handler"] = handler
    history = await group_chat(client, key["key"])
    await delivered(app, writes)
    if change == "edit_before_bob":
        history = [history[0], {"role": "assistant", "content": "edited"}, *history[2:]]
    else:
        history = history[2:]
    await chat(client, key["key"], [*history, REPLY, user("I am Dave")], "dave")
    first = sessions[0]
    writes.clear()
    sessions.clear()
    messages = await delivered(app, writes)
    assert sessions and first not in sessions
    users = [(m["parts"][0]["text"], m.get("peer_id")) for m in messages if m["role"] == "user"]
    expected = [("I am Alice", "alice"), ("I am Bob", "bob"), ("I am Carol", "carol")]
    assert users == expected[2 - len(history) // 2 :]


async def test_repeated_text_keeps_each_senders_peer(running_gateway):
    app, client, _, key, _, writes = running_gateway
    history = []
    for peer in ("alice", "bob", "carol"):
        history = [*history, *([REPLY] if history else []), user("+1")]
        await chat(client, key["key"], history, peer)
    messages = await delivered(app, writes)
    assert [m.get("peer_id") for m in messages if m["role"] == "user"] == ["alice", "bob"]


@pytest.mark.parametrize("cause", ["edit", "reset"])
async def test_saving_again_keeps_each_messages_peer(running_gateway, cause):
    """Saving the history to a new OpenViking session keeps each message's peer."""
    app, client, admin, key, _, writes = running_gateway
    sessions = []

    async def handler(request):
        if request.path.endswith("/messages/batch"):
            sessions.append(request.path.split("/")[4])

    app.state.test_backend["handler"] = handler
    await chat(client, key["key"], [user("I am Alice")], "alice-bot")
    await chat(client, key["key"], [user("I am Alice"), REPLY, user("I am Bob")], "bob-bot")
    await delivered(app, writes)
    reply = REPLY
    if cause == "edit":
        reply = {"role": "assistant", "content": "edited"}
    else:
        logs = (await client.get("/admin/logs", headers=admin)).json()
        path = "/admin/keys/" + logs[0]["credential_id"] + "/capture/reset"
        body = {"session": "shared", "protocol": "chat"}
        assert (await client.post(path, headers=admin, json=body)).status_code == 200
    await chat(client, key["key"], [user("I am Alice"), reply, user("Carol")], "carol-bot")
    writes.clear()
    messages = await delivered(app, writes)
    assert len(set(sessions)) == 2
    users = [(m["parts"][0]["text"], m.get("peer_id")) for m in messages if m["role"] == "user"]
    assert users[0] == ("I am Alice", "alice-bot")


async def test_anonymous_history_continues_across_peers(setup_kernel, credential, policy):
    kernel, _, _, _ = setup_kernel
    first = [user("How do we deploy?")]
    alice = await kernel.prepare(
        {"messages": first}, "chat", {}, credential, {"id": "u"}, policy, peer="alice-bot"
    )
    reply = {"role": "assistant", "content": "Use the blue cluster."}
    await kernel.completed(alice, credential, ResponseCapture("chat", reply, complete=True))
    later = [*first, reply, user("And roll back?")]
    bob = await kernel.prepare(
        {"messages": later}, "chat", {}, credential, {"id": "u"}, policy, peer="bob-bot"
    )
    assert alice.anonymous and bob.session == alice.session


async def test_gateway_tool_calls_do_not_carry_the_peer(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    policy.update(gateway_tools=True)
    calls = []
    for peer in ("review-agent", None):
        prepared = await kernel.prepare(
            {"messages": [{"role": "user", "content": "Find the deploy notes"}]},
            "chat",
            {"x-openviking-session": "tools-" + str(peer)},
            credential,
            {"id": "u"},
            policy,
            peer=peer,
        )
        assert prepared.tools_active
        viking.mcp = AsyncMock(return_value={"content": [{"type": "text", "text": "found"}]})
        call = {
            "id": "call-1",
            "function": {"name": "openviking_find", "arguments": '{"query":"deploy"}'},
        }
        result = await ToolExecutor(viking, store, prepared, credential, "", 1024).execute(call)
        assert not result["failed"]
        calls.append(viking.mcp.call_args.kwargs)
    # Like the memory plugins' MCP proxy by default, tools are not narrowed to the peer.
    assert calls == [{}, {}]
    assert viking.recall_peers == ["review-agent", None]
