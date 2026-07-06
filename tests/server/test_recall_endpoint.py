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


async def test_recall_endpoint_searches_by_type_quota_and_renders(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    calls = []

    async def fake_find(**kwargs):
        calls.append(kwargs)
        target_uri = kwargs["target_uri"]
        if target_uri.endswith("/events"):
            return _FakeFindResult(
                [
                    _memory(
                        "viking://user/test_user/memories/events/launch.md",
                        0.91,
                        "Launch decision",
                    )
                ]
            )
        if target_uri.endswith("/entities"):
            return _FakeFindResult(
                [
                    _memory(
                        "viking://user/test_user/memories/entities/openviking.md",
                        0.82,
                        "OpenViking project",
                    )
                ]
            )
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        if uri.endswith("/launch.md"):
            # Too large for the full-content budget -> degrades to summary.
            return "Summary: Ship stdio MCP proxy.\n2026-07-06 ChatLog: " + "x" * 2000
        return "OpenViking is the target project."

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)

    resp = await client.post(
        "/api/v1/search/recall",
        json={
            "query": "what should I remember",
            "quotas": {"events": 1, "entities": 1, "preferences": 0, "experiences": 0},
            "max_chars": 400,
            "min_score": 0.1,
            "render": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["stats"]["returned"] == 2
    assert [entry["type"] for entry in result["entries"]] == ["events", "entities"]
    assert '<memory_group type="events"' in result["rendered"]
    assert "<summary>Ship stdio MCP proxy.</summary>" in result["rendered"]
    assert "viking://user/test_user/memories/entities/openviking.md" in result["rendered"]
    assert [call["target_uri"].rsplit("/", 1)[-1] for call in calls] == ["events", "entities"]


async def test_recall_endpoint_respects_max_chars_budget(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_find(**kwargs):
        if kwargs["target_uri"].endswith("/events"):
            return _FakeFindResult(
                [
                    _memory("viking://user/test_user/memories/events/big.md", 0.9, "big event"),
                    _memory("viking://user/test_user/memories/events/big2.md", 0.8, "second"),
                ]
            )
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        del kwargs
        # Full content and even its summary are far beyond the budget below.
        return "Summary: " + ("s" * 500) + "\ndetails: " + ("x" * 2000) + f"\n{uri}"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)

    # Budget fits the URI fallback but not full content or the summary:
    # the entry must degrade all the way to a uri fragment.
    resp = await client.post(
        "/api/v1/search/recall",
        json={
            "query": "budget",
            "quotas": {"events": 2, "entities": 0, "preferences": 0},
            "max_chars": 120,
            "min_score": 0.1,
            "render": True,
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert "<summary>" not in result["rendered"]
    assert all(entry["mode"] == "uri" for entry in result["entries"])
    fragment_chars = sum(len(e["uri"]) for e in result["entries"])
    assert fragment_chars <= 120
    assert result["stats"]["returned"] + result["stats"]["dropped"] == 2

    # Budget too small for even one uri fragment: everything is dropped
    # instead of rendering past the contract.
    resp = await client.post(
        "/api/v1/search/recall",
        json={
            "query": "budget",
            "quotas": {"events": 2, "entities": 0, "preferences": 0},
            "max_chars": 10,
            "min_score": 0.1,
            "render": True,
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["rendered"] == ""
    assert result["entries"] == []
    assert result["stats"]["dropped"] == 2


async def test_recall_endpoint_sanitizes_nonfinite_scores(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_find(**kwargs):
        if kwargs["target_uri"].endswith("/events"):
            return _FakeFindResult(
                [
                    _memory(
                        "viking://user/test_user/memories/events/inf.md",
                        float("inf"),
                        "bad score",
                    )
                ]
            )
        return _FakeFindResult([])

    async def fake_read(uri, **kwargs):
        del uri, kwargs
        return "small content"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)

    resp = await client.post(
        "/api/v1/search/recall",
        json={"query": "inf", "quotas": {"events": 1, "entities": 0, "preferences": 0}},
    )

    assert resp.status_code == 200
    entries = resp.json()["result"]["entries"]
    assert entries and entries[0]["score"] == 0.0


async def test_recall_endpoint_rejects_unknown_fields(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/search/recall",
        json={"query": "hello", "unexpected": "value"},
    )

    assert resp.status_code == 400


async def test_recall_endpoint_filters_profile_and_duplicates(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_find(**kwargs):
        del kwargs
        duplicate = _memory("viking://user/test_user/memories/events/dup.md", 0.8, "same")
        profile = _memory("viking://user/test_user/memories/profile.md", 0.99, "profile")
        return _FakeFindResult([profile, duplicate, duplicate])

    async def fake_read(uri, **kwargs):
        del kwargs
        if uri.endswith("profile.md"):
            return "profile"
        return "duplicate content"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)

    resp = await client.post(
        "/api/v1/search/recall",
        json={"query": "hello", "quotas": {"events": 3, "entities": 0, "preferences": 0}},
    )

    assert resp.status_code == 200
    entries = resp.json()["result"]["entries"]
    assert [entry["uri"] for entry in entries] == ["viking://user/test_user/memories/events/dup.md"]
