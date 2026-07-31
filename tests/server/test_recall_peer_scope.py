# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import httpx

from openviking.retrieve.type_quota_recall import normalize_penalties
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.mcp_endpoint import _mcp_ctx
from openviking_cli.retrieve import ContextType, MatchedContext
from openviking_cli.session.user_id import UserIdentifier


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


def _self_memory_target(target_uri: str, memory_type: str) -> bool:
    return target_uri.endswith(f"/memories/{memory_type}") and "/peers/" not in target_uri


def test_normalize_penalties_defaults_scalar_dict_and_clamp():
    assert normalize_penalties() == {
        "events": 0.1,
        "entities": 0.1,
        "preferences": 0.02,
        "experiences": 0.02,
    }
    assert normalize_penalties(0.2) == {
        "events": 0.2,
        "entities": 0.2,
        "preferences": 0.2,
        "experiences": 0.2,
    }
    assert normalize_penalties({"events": 2, "preferences": -1, "unknown": 0.5}) == {
        "events": 1.0,
        "entities": 0.1,
        "preferences": 0.0,
        "experiences": 0.02,
    }


async def test_recall_default_all_searches_other_peers_and_reads_with_open_ctx(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    calls = []
    read_calls = []

    async def fake_find(**kwargs):
        calls.append(kwargs)
        target_uri = kwargs["target_uri"]
        if _self_memory_target(target_uri, "events"):
            return _FakeFindResult([_memory(f"{target_uri}/global.md", 0.8, "global")])
        if target_uri.endswith("/peers/current/memories/events"):
            return _FakeFindResult(
                [
                    _memory(
                        f"{target_uri}/current.md",
                        0.91,
                        "current",
                    )
                ]
            )
        if target_uri.endswith("/peers"):
            return _FakeFindResult(
                [
                    _memory(
                        f"{target_uri}/other/memories/events/other.md",
                        0.89,
                        "other",
                    ),
                    _memory(
                        f"{target_uri}/other/resources/doc.md",
                        0.99,
                        "resource ignored",
                    ),
                    _memory(
                        f"{target_uri}/current/memories/events/current-dup.md",
                        0.99,
                        "actor ignored from open peers",
                    ),
                ]
            )
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        read_calls.append((uri, kwargs.get("ctx")))
        return f"content for {uri}"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)

    resp = await client.post(
        "/api/v1/search/recall",
        headers={"X-OpenViking-Actor-Peer": "current"},
        json={
            "query": "peer memory",
            "quotas": {"events": 3, "entities": 0, "preferences": 0, "experiences": 0},
            "max_chars": 5000,
        },
    )

    assert resp.status_code == 200
    result = resp.json()["result"]
    assert [entry["origin"] for entry in result["entries"]] == [
        "actor_peer",
        "self",
        "other_peer",
    ]
    assert result["stats"]["peer_scope"] == "all"
    assert result["stats"]["origins"] == {"actor_peer": 1, "self": 1, "other_peer": 1}
    assert '<memory_section source="current-project">' in result["rendered"]
    assert '<memory_section source="global">' in result["rendered"]
    assert '<memory_section source="other-projects">' in result["rendered"]

    peer_root_call = next(call for call in calls if call["target_uri"].endswith("/peers"))
    assert peer_root_call["ctx"].actor_peer_id is None
    other_read_ctx = next(ctx for uri, ctx in read_calls if uri.endswith("/other.md"))
    assert other_read_ctx.actor_peer_id is None


async def test_recall_actor_scope_keeps_legacy_rendering(
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

    resp = await client.post(
        "/api/v1/search/recall",
        headers={"X-OpenViking-Actor-Peer": "current"},
        json={
            "query": "peer memory",
            "peer_scope": "actor",
            "quotas": {"events": 1, "entities": 0, "preferences": 0, "experiences": 0},
            "max_chars": 300,
        },
    )

    assert resp.status_code == 200
    result = resp.json()["result"]
    assert '<memory_group type="events"' in result["rendered"]
    assert "<memory_section" not in result["rendered"]
    assert all(not call["target_uri"].endswith("/peers") for call in calls)


async def test_recall_other_peer_penalty_is_type_aware(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_find(**kwargs):
        target_uri = kwargs["target_uri"]
        if target_uri.endswith("/peers/current/memories/events"):
            return _FakeFindResult([_memory(f"{target_uri}/actor-event.md", 0.86)])
        if target_uri.endswith("/peers/current/memories/experiences"):
            return _FakeFindResult(
                [
                    _memory(
                        f"{target_uri}/actor-exp.md",
                        0.86,
                    )
                ]
            )
        if target_uri.endswith("/peers"):
            return _FakeFindResult(
                [
                    _memory(
                        f"{target_uri}/other/memories/events/other-event.md",
                        0.90,
                    ),
                    _memory(
                        f"{target_uri}/other/memories/experiences/other-exp.md",
                        0.90,
                    ),
                ]
            )
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        del kwargs
        return f"content {uri}"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)

    resp = await client.post(
        "/api/v1/search/recall",
        headers={"X-OpenViking-Actor-Peer": "current"},
        json={
            "query": "ranking",
            "quotas": {"events": 1, "entities": 0, "preferences": 0, "experiences": 1},
            "max_chars": 5000,
        },
    )

    assert resp.status_code == 200
    entries = resp.json()["result"]["entries"]
    assert [entry["uri"].rsplit("/", 1)[-1] for entry in entries] == [
        "actor-event.md",
        "other-exp.md",
    ]
    assert [entry["origin"] for entry in entries] == ["actor_peer", "other_peer"]


async def test_recall_accepts_scalar_penalty_override(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_find(**kwargs):
        target_uri = kwargs["target_uri"]
        if target_uri.endswith("/peers/current/memories/events"):
            return _FakeFindResult([_memory(f"{target_uri}/actor.md", 0.86)])
        if target_uri.endswith("/peers"):
            return _FakeFindResult([_memory(f"{target_uri}/other/memories/events/other.md", 0.90)])
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        del kwargs
        return f"content {uri}"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)

    resp = await client.post(
        "/api/v1/search/recall",
        headers={"X-OpenViking-Actor-Peer": "current"},
        json={
            "query": "ranking",
            "quotas": {"events": 1, "entities": 0, "preferences": 0, "experiences": 0},
            "other_peer_penalty": 0.0,
        },
    )

    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["entries"][0]["uri"].endswith("/other.md")
    assert result["stats"]["other_peer_penalties"]["events"] == 0.0


async def test_recall_rejects_unknown_peer_scope(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/search/recall",
        json={"query": "hello", "peer_scope": "bogus"},
    )

    assert resp.status_code == 400


async def test_mcp_recall_tool_passes_peer_scope_and_penalty(service, monkeypatch):
    captured = {}
    set_service(service)
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("test_user"),
        role=Role.ROOT,
    )
    token = _mcp_ctx.set(ctx)

    async def fake_search_type_quota_recall(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(rendered='<memory_section source="other-projects"></memory_section>')

    monkeypatch.setattr(
        "openviking.server.mcp_endpoint.search_type_quota_recall",
        fake_search_type_quota_recall,
    )

    from openviking.server.mcp_endpoint import recall

    try:
        result = await recall(
            query="hello",
            quotas={"events": 1},
            peer_scope="actor",
            other_peer_penalty={"events": 0.5},
        )
    finally:
        _mcp_ctx.reset(token)

    assert "other-projects" in result
    assert captured["peer_scope"] == "actor"
    assert captured["other_peer_penalty"] == {"events": 0.5}
