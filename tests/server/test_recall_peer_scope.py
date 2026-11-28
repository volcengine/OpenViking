# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx

from openviking.server.identity import RequestContext, Role
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
    root = "viking://user/default"
    content = {
        f"{root}/memories/events/global.md": "Owner-wide release details.",
        f"{root}/peers/current/memories/events/current.md": "Current peer release details.",
        f"{root}/peers/other/memories/events/other.md": "Other peer release details.",
    }
    seed_ctx = RequestContext(user=service.user, role=Role.ROOT)
    for uri, text in content.items():
        await service.viking_fs.write_file(uri, text, ctx=seed_ctx)
    read_visible = service.fs.read_visible

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

    async def observe_read(uri, **kwargs):
        read_calls.append((uri, kwargs["ctx"]))
        return await read_visible(uri, **kwargs)

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read_visible", observe_read)
    payload = {
        "query": "peer memory",
        "quotas": {"events": 3, "entities": 0, "preferences": 0, "experiences": 0},
        "max_chars": 5000,
    }
    headers = {"X-OpenViking-Actor-Peer": "current"}
    response = await client.post("/api/v1/search/recall", headers=headers, json=payload)

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

    assert {entry["uri"]: entry["text"] for entry in result["entries"]} == content

    calls.clear()
    read_calls.clear()
    restricted = await client.post(
        "/api/v1/search/recall", headers=headers, json={**payload, "peer_scope": "actor"}
    )
    assert restricted.status_code == 200, restricted.text
    entries = restricted.json()["result"]["entries"]
    assert [entry["origin"] for entry in entries] == ["actor_peer", "self"]
    assert all(not call["target_uri"].endswith("/peers") for call in calls)
    assert all(not uri.endswith("/other.md") for uri, _ in read_calls)
