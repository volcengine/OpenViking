# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx

from openviking_cli.retrieve import ContextType, MatchedContext


class _FakeFindResult:
    def __init__(self, memories):
        self.memories = memories


def _memory(uri: str, score: float = 0.9, abstract: str = ""):
    return MatchedContext(
        uri=uri,
        context_type=ContextType.MEMORY,
        level=2,
        score=score,
        abstract=abstract,
        category=uri.split("/memories/", 1)[-1].split("/", 1)[0],
    )


def _self_memory_target(target_uri: str) -> bool:
    return target_uri.endswith("/memories/events") and "/peers/" not in target_uri


async def test_default_scope_searches_other_peers_with_an_open_context(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    calls = []
    read_calls = []

    async def fake_find(**kwargs):
        calls.append(kwargs)
        target_uri = kwargs["target_uri"]
        if _self_memory_target(target_uri):
            return _FakeFindResult([_memory(f"{target_uri}/global.md", 0.8, "global")])
        if target_uri.endswith("/peers/current/memories/events"):
            return _FakeFindResult([_memory(f"{target_uri}/current.md", 0.91, "current")])
        if target_uri.endswith("/peers"):
            return _FakeFindResult(
                [_memory(f"{target_uri}/other/memories/events/other.md", 0.89, "other")]
            )
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        read_calls.append((uri, kwargs.get("ctx")))
        return f"content for {uri}"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)
    response = await client.post(
        "/api/v1/search/recall",
        headers={"X-OpenViking-Actor-Peer": "current"},
        json={
            "query": "peer memory",
            "quotas": {"events": 3, "entities": 0, "preferences": 0, "experiences": 0},
            "max_chars": 5000,
        },
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert [entry["origin"] for entry in result["entries"]] == [
        "actor_peer",
        "self",
        "other_peer",
    ]
    peer_call = next(call for call in calls if call["target_uri"].endswith("/peers"))
    assert peer_call["ctx"].actor_peer_id is None
    other_read_ctx = next(ctx for uri, ctx in read_calls if uri.endswith("/other.md"))
    assert other_read_ctx.actor_peer_id is None


async def test_actor_scope_skips_the_open_peer_scan(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_find(**kwargs):
        target = kwargs["target_uri"]
        if target.endswith("/peers/current/memories/events"):
            return _FakeFindResult([_memory(f"{target}/current.md", 0.8, "Actor event")])
        if target.endswith("/peers"):
            return _FakeFindResult(
                [_memory(f"{target}/other/memories/events/other.md", 0.9, "Other peer event")]
            )
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        label = "Actor event" if uri.endswith("/current.md") else "Other peer event"
        return f"# Summary\n{label}"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)
    headers = {"X-OpenViking-Actor-Peer": "current"}
    payload = {
        "query": "peer memory",
        "quotas": {"events": 2, "entities": 0, "preferences": 0, "experiences": 0},
        "max_chars": 5000,
    }

    all_peers = await client.post("/api/v1/search/recall", headers=headers, json=payload)
    assert all_peers.status_code == 200, all_peers.text
    entries = {entry["origin"]: entry["uri"] for entry in all_peers.json()["result"]["entries"]}
    assert set(entries) == {"actor_peer", "other_peer"}
    actor_uri = entries["actor_peer"]
    other_uri = entries["other_peer"]
    assert actor_uri.endswith("/peers/current/memories/events/current.md")
    assert other_uri.endswith("/peers/other/memories/events/other.md")

    actor = await client.post(
        "/api/v1/search/recall", headers=headers, json={**payload, "peer_scope": "actor"}
    )
    assert actor.status_code == 200, actor.text
    result = actor.json()["result"]
    assert [(entry["uri"], entry["origin"]) for entry in result["entries"]] == [
        (actor_uri, "actor_peer")
    ]
    assert "Actor event" in result["rendered"]
    assert other_uri not in result["rendered"]
