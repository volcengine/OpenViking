"""Peer scoping: X-OpenViking-Actor-Peer and a gateway key's default peer."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking_gateway.protocols import peer_session
from openviking_gateway.storage import digest
from openviking_gateway.tool_executor import ToolExecutor
from openviking_gateway.tool_protocols import ResponseCapture

PEER = "X-OpenViking-Actor-Peer"


def observe(app):
    """Record the headers of the gateway's OpenViking calls, keyed by path kind."""
    calls = {"search": [], "write": [], "mcp": []}

    async def handler(request):
        if request.path == "/api/v1/search/search":
            calls["search"].append(dict(request.headers))
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


async def test_header_scopes_recall_and_names_the_captured_user(running_gateway):
    app, client, admin, key, seen, writes = running_gateway
    calls = observe(app)
    await two_turns(client, key["key"], {PEER: " review-agent "})
    assert calls["search"] and all(header(h) == "review-agent" for h in calls["search"])
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


async def test_peers_sharing_a_session_header_get_their_own_sessions(running_gateway):
    app, client, admin, key, _, _ = running_gateway
    calls = observe(app)
    for peer in ("alice-bot", "bob-bot", None):
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + key["key"],
                "x-openviking-session": "shared",
                **({PEER: peer} if peer else {}),
            },
            json={"model": "model", "messages": [{"role": "user", "content": "How do we deploy?"}]},
        )
        assert response.status_code == 200, response.text
    # Each conversation recalls once: none replays another peer's injection.
    assert [header(h) for h in calls["search"]] == ["alice-bot", "bob-bot", None]
    logs = (await client.get("/admin/logs", headers=admin)).json()
    sessions = {log.get("peer"): log["session"] for log in logs}
    assert sessions == {
        "alice-bot": peer_session(digest("shared"), "alice-bot"),
        "bob-bot": peer_session(digest("shared"), "bob-bot"),
        None: digest("shared"),
    }
    path = "/admin/keys/" + logs[0]["credential_id"] + "/capture/reset"
    for body in (
        {"session": "shared", "protocol": "chat", "peer_id": "alice-bot"},
        {"session": sessions["bob-bot"], "protocol": "chat"},
        {"session": "shared", "protocol": "chat"},
    ):
        assert (await client.post(path, headers=admin, json=body)).status_code == 200
    unknown = {"session": "shared", "protocol": "chat", "peer_id": "carol-bot"}
    assert (await client.post(path, headers=admin, json=unknown)).status_code == 404


async def test_anonymous_history_does_not_continue_another_peers_session(
    setup_kernel, credential, policy
):
    kernel, _, _, _ = setup_kernel
    first = [{"role": "user", "content": "How do we deploy?"}]
    alice = await kernel.prepare(
        {"messages": first}, "chat", {}, credential, {"id": "u"}, policy, peer="alice-bot"
    )
    reply = {"role": "assistant", "content": "Use the blue cluster."}
    await kernel.completed(alice, credential, ResponseCapture("chat", reply, complete=True))
    later = [*first, reply, {"role": "user", "content": "And roll back?"}]
    same = await kernel.prepare(
        {"messages": later}, "chat", {}, credential, {"id": "u"}, policy, peer="alice-bot"
    )
    other = await kernel.prepare(
        {"messages": later}, "chat", {}, credential, {"id": "u"}, policy, peer="bob-bot"
    )
    assert alice.anonymous and same.session == alice.session
    assert other.session != alice.session and other.session.startswith(peer_session("", "bob-bot"))


async def test_gateway_tool_calls_carry_the_peer(setup_kernel, credential, policy):
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
    assert calls == [{"peer": "review-agent"}, {}]
    assert viking.recall_peers == ["review-agent", None]
