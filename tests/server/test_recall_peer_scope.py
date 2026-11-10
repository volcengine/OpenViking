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
    calls = []

    async def fake_find(**kwargs):
        calls.append(kwargs)
        if kwargs["target_uri"].endswith("/events"):
            return _FakeFindResult(
                [_memory("viking://user/test_user/peers/current/memories/events/current.md")]
            )
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        del uri, kwargs
        return "Summary: actor only.\n2026-07-09 ChatLog: details"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)
    response = await client.post(
        "/api/v1/search/recall",
        headers={"X-OpenViking-Actor-Peer": "current"},
        json={
            "query": "peer memory",
            "peer_scope": "actor",
            "quotas": {"events": 1, "entities": 0, "preferences": 0, "experiences": 0},
            "max_chars": 300,
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["rendered"].count("<memory ") == 1
    assert all(not call["target_uri"].endswith("/peers") for call in calls)
