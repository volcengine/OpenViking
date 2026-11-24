"""Peer-only profiles use the regular Gateway request and session APIs."""

import asyncio

from aiohttp import web

from openviking_gateway.proxy import context_override

PARENT = "viking://user/alice/memories"
PEER_MEMORY_HEADER = "X-OpenViking-Peer-Allow-User-Memory"


def memory_root(user, peer):
    return f"viking://user/{user}/peers/{peer}/memories"


def enable_peer_memory_backend(app):
    app.state.test_backend["supports_peer_memory_only"] = True
    app.state.viking.identities.clear()


def peer_headers(key, peer):
    return {
        "Authorization": "Bearer " + key["key"],
        "X-OpenViking-Actor-Peer": peer,
        "X-OpenViking-Session": f"session-{peer}",
        "X-OpenViking-Context": '{"recall_peer_scope":"actor","peer_allow_user_memory":false}',
    }


async def test_peer_only_profile_keeps_read_and_write_in_the_peer(running_gateway):
    app, client, _, key, seen, writes = running_gateway
    enable_peer_memory_backend(app)
    recalls, profiles, policies, session_peers = {}, set(), {}, {}

    async def backend(request):
        peer = request.headers.get("X-OpenViking-Actor-Peer")
        if request.path == "/api/v1/search/search":
            assert request.headers[PEER_MEMORY_HEADER] == "false"
            recalls[peer] = await request.json()
            uri = memory_root("alice", peer) + "/events/own.md"
            return web.json_response(
                {
                    "result": {"entries": [{"uri": uri}], "rendered": f"memory from {peer}"},
                }
            )
        if request.path == "/api/v1/content/read":
            assert request.headers[PEER_MEMORY_HEADER] == "false"
            profiles.add(request.query["uri"])
        if request.path == "/api/v1/sessions":
            body = await request.json()
            policies[body["session_id"]] = body["peer_allow_user_memory"]
        if request.path.endswith("/messages/batch"):
            session = request.path.split("/")[4]
            session_peers.setdefault(session, set()).update(
                message["peer_id"]
                for message in (await request.json())["messages"]
                if message["role"] == "user"
            )
        return None

    app.state.test_backend["handler"] = backend
    replies = await asyncio.gather(
        *(
            client.post(
                "/v1/chat/completions",
                headers=peer_headers(key, peer),
                json={
                    "model": "model",
                    "messages": [{"role": "user", "content": f"Input marker for {peer}"}],
                },
            )
            for peer in ("trial-a", "trial-b")
        )
    )
    assert all(reply.status_code == 200 for reply in replies)
    assert set(recalls) == {"trial-a", "trial-b"}
    for peer in recalls:
        assert memory_root("alice", peer) + "/profile.md" in profiles
    assert all(not uri.startswith(PARENT) for uri in profiles)
    payloads = [raw for path, raw, _ in seen if path.endswith("/chat/completions")]
    assert len(payloads) == 2
    for peer, other in (("trial-a", "trial-b"), ("trial-b", "trial-a")):
        [payload] = [raw for raw in payloads if f"memory from {peer}".encode() in raw]
        assert f"memory from {other}".encode() not in payload
        assert f"Input marker for {peer}".encode() in payload
        assert f"Input marker for {other}".encode() not in payload
    assert len(policies) == 2
    assert session_peers.keys() == policies.keys()
    assert {frozenset(peers) for peers in session_peers.values()} == {
        frozenset({"trial-a"}),
        frozenset({"trial-b"}),
    }
    assert all(value is False for value in policies.values())
    assert {
        message["peer_id"]
        for batch in writes
        for message in batch["messages"]
        if message["role"] == "user"
    } == {"trial-a", "trial-b"}


async def test_peer_only_profile_rejects_an_older_backend(running_gateway):
    _, client, _, key, seen, _ = running_gateway
    reply = await client.post(
        "/v1/chat/completions",
        headers=peer_headers(key, "trial-a"),
        json={"model": "model", "messages": [{"role": "user", "content": "Recall deployment"}]},
    )
    assert reply.status_code == 503
    assert reply.json()["reason"] == "openviking_peer_memory_unsupported"
    assert not seen


def test_profile_restriction_cannot_be_relaxed_by_client():
    policy, _ = context_override(
        {"peer_allow_user_memory": False}, '{"peer_allow_user_memory":true}'
    )
    assert policy["peer_allow_user_memory"] is False


async def test_peer_only_requests_disable_stored_provider_responses(running_gateway):
    app, client, _, key, seen, _ = running_gateway
    enable_peer_memory_backend(app)
    headers = peer_headers(key, "trial-a")
    reply = await client.post(
        "/v1/responses",
        headers=headers,
        json={"model": "model", "input": "Hello", "store": True},
    )
    assert reply.status_code == 200
    assert b'"store":false' in seen[-1][1]
    for peer in ("trial-a", "trial-b"):
        response = await client.get("/v1/responses/resp-1", headers=peer_headers(key, peer))
        assert response.status_code == 404
    stateful = await client.post(
        "/v1/responses",
        headers=headers,
        json={"model": "model", "input": "Continue", "previous_response_id": "resp-1"},
    )
    assert stateful.status_code == 503
    compact = await client.post(
        "/v1/responses/compact",
        headers=headers,
        json={"model": "model", "input": "Continue", "previous_response_id": "resp-1"},
    )
    assert compact.status_code == 400
    referenced = await client.post(
        "/v1/responses",
        headers=headers,
        json={"model": "model", "input": [{"type": "item_reference", "id": "item-trip"}]},
    )
    assert referenced.status_code == 400
    assert len(seen) == 1


async def test_restricted_session_does_not_replay_user_memory(running_gateway):
    app, client, _, key, seen, _ = running_gateway
    enable_peer_memory_backend(app)

    async def backend(request):
        if request.path == "/api/v1/search/search":
            if request.headers.get(PEER_MEMORY_HEADER) == "false":
                return web.json_response({"result": {"entries": [], "rendered": ""}})
            return web.json_response(
                {
                    "result": {
                        "entries": [{"uri": PARENT + "/events/shared.md"}],
                        "rendered": "cached shared memory",
                    },
                }
            )

    app.state.test_backend["handler"] = backend
    headers = peer_headers(key, "trial-a")
    body = {"model": "model", "messages": [{"role": "user", "content": "Recall deployment"}]}
    normal = dict(headers)
    del normal["X-OpenViking-Context"]
    assert (await client.post("/v1/chat/completions", headers=normal, json=body)).status_code == 200
    assert (
        await client.post("/v1/chat/completions", headers=headers, json=body)
    ).status_code == 200
    sent = [raw for path, raw, _ in seen if path.endswith("/chat/completions")]
    assert len(sent) == 2
    assert b"cached shared memory" in sent[0]
    assert b"cached shared memory" not in sent[1]
