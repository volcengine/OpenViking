# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import httpx

from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve import ContextType, MatchedContext
from openviking_cli.session.user_id import UserIdentifier


class _FakeFindResult:
    def __init__(self, memories=None):
        self.memories = memories or []
        self.resources = []
        self.skills = []


def _memory(uri: str, score: float = 0.9, abstract: str = "", level: int = 2):
    return MatchedContext(
        uri=uri,
        context_type=ContextType.MEMORY,
        level=level,
        score=score,
        abstract=abstract,
        category=uri.split("/memories/", 1)[-1].split("/", 1)[0],
    )


async def test_context_mode_returns_flat_entries_within_budget(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_find(**kwargs):
        del kwargs
        return _FakeFindResult(
            [
                _memory("viking://user/test_user/memories/events/launch.md", 0.71, "Launch"),
                _memory("viking://user/test_user/memories/entities/ov.md", 0.55, "OpenViking"),
            ]
        )

    async def fake_read(uri, **kwargs):
        del kwargs
        return f"# Summary\ngist of {uri}\n\n# ChatLog:\n" + "x" * 300

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)

    response = await client.post(
        "/api/v1/search/search",
        json={"query": "what changed", "mode": "context", "max_tokens": 1200},
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert [entry["category"] for entry in result["entries"]] == ["events", "entities"]
    assert all(entry["detail"] != "uri" for entry in result["entries"])
    assert result["rendered"].count("<memory ") == 2
    assert result["stats"]["used_tokens"] <= 1200


async def test_context_mode_quotas_use_category_ownership_roots(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    targets = []

    async def fake_find(**kwargs):
        targets.append(kwargs["target_uri"])
        return _FakeFindResult()

    monkeypatch.setattr(service.search, "find", fake_find)
    response = await client.post(
        "/api/v1/search/search",
        json={
            "query": "quota",
            "mode": "context",
            "quotas": {"events": 2, "skills": 1},
            "peer_scope": "actor",
        },
    )

    assert response.status_code == 200
    assert any(target.endswith("/memories/events") for target in targets)
    assert any(target.endswith("/user/default/skills") for target in targets)
    assert "viking://agent/skills" in targets


async def test_coding_purpose_searches_all_domains_and_actor_resource(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    calls = []

    async def fake_find(**kwargs):
        calls.append(kwargs)
        return _FakeFindResult()

    monkeypatch.setattr(service.search, "find", fake_find)
    response = await client.post(
        "/api/v1/search/search",
        headers={"X-OpenViking-Actor-Peer": "current"},
        json={
            "query": "release",
            "mode": "context",
            "purpose": "coding",
            "limit": 1,
            "peer_scope": "actor",
        },
    )

    assert response.status_code == 200
    targets = [call["target_uri"] for call in calls]
    assert any(target.endswith("/memories/events") for target in targets)
    assert any(target.endswith("/memories/entities") for target in targets)
    assert any(target.endswith("/memories/preferences") for target in targets)
    assert any(target.endswith("/memories/experiences") for target in targets)
    assert "viking://resources" in targets
    assert any(target.endswith("/peers/current/resources") for target in targets)
    assert any(target.endswith("/skills") for target in targets)
    assert response.json()["result"]["stats"]["quotas"] == {
        "events": 1,
        "entities": 2,
        "preferences": 1,
        "experiences": 1,
        "resources": 3,
        "skills": 2,
    }
    assert response.json()["result"]["stats"]["ignored"] == ["limit"]


async def test_context_and_list_default_limit_is_ten(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    limits = []

    async def fake_find(**kwargs):
        limits.append(("context", kwargs["limit"]))
        return _FakeFindResult()

    async def fake_search(**kwargs):
        limits.append(("list", kwargs["limit"]))
        return _FakeFindResult()

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.search, "search", fake_search)

    context_response = await client.post(
        "/api/v1/search/search",
        json={"query": "defaults", "mode": "context"},
    )
    list_response = await client.post(
        "/api/v1/search/search",
        json={"query": "defaults"},
    )

    assert context_response.status_code == 200
    assert list_response.status_code == 200
    assert ("context", 10) in limits
    assert ("list", 10) in limits


async def test_context_and_list_parameter_domains_do_not_overlap(client: httpx.AsyncClient):
    list_response = await client.post(
        "/api/v1/search/search",
        json={"query": "hello", "max_tokens": 1000},
    )
    context_response = await client.post(
        "/api/v1/search/search",
        json={
            "query": "hello",
            "mode": "context",
            "target_uri": "viking://user/test_user",
        },
    )

    assert list_response.status_code == 400
    assert "mode='context'" in list_response.text
    assert context_response.status_code == 400
    assert "target_uri" in context_response.text


async def test_context_mode_rejects_a_request_list_mode_rejects(client: httpx.AsyncClient):
    list_response = await client.post("/api/v1/search/search", json={})
    context_response = await client.post("/api/v1/search/search", json={"mode": "context"})

    assert list_response.status_code == 400
    assert context_response.status_code == 400
    assert context_response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_context_mode_degrades_a_retrieval_failure(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def fake_find(**kwargs):
        del kwargs
        raise RuntimeError("embedder unreachable")

    monkeypatch.setattr(service.search, "find", fake_find)
    response = await client.post(
        "/api/v1/search/search",
        json={"query": "still a valid request", "mode": "context"},
    )

    assert response.status_code == 200
    assert response.json()["result"]["stats"]["retrieval_errors"]


async def test_list_mode_response_is_unchanged(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    hit = _memory("viking://user/test_user/memories/events/a.md", 0.5, "abs")

    async def fake_result(**kwargs):
        del kwargs
        return _FakeFindResult([hit])

    monkeypatch.setattr(service.search, "find", fake_result)
    monkeypatch.setattr(service.search, "search", fake_result)

    default = await client.post("/api/v1/search/search", json={"query": "plain"})
    explicit = await client.post(
        "/api/v1/search/search",
        json={"query": "plain", "mode": "list"},
    )

    assert default.status_code == 200
    assert default.json() == explicit.json()
    assert "rendered" not in default.json()["result"]


async def test_context_mode_reads_real_agfs_content_and_sidecars(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    ctx = RequestContext(user=UserIdentifier.the_default_user("default"), role=Role.ROOT)
    root = "viking://user/default/memories"
    file_uri = f"{root}/events/launch.md"
    directory_uri = f"{root}/entities"
    await service.viking_fs.write_file(
        uri=file_uri,
        content=(
            "# Summary\nShipped the stdio MCP proxy.\n\n"
            "# 2026-07-14 ChatLog:\n"
            + ("chatter " * 400)
            + '\n\n<!-- MEMORY_FIELDS\n{"memory_type": "events"}\n-->'
        ),
        ctx=ctx,
    )
    await service.viking_fs.write_file(
        uri=f"{directory_uri}/.overview.md",
        content="Entities directory: projects, people and software.",
        ctx=ctx,
    )

    async def fake_find(**kwargs):
        del kwargs
        return _FakeFindResult(
            [
                _memory(file_uri, 0.62, "launch abstract"),
                _memory(f"{directory_uri}/.abstract.md", 0.44, "", level=0),
            ]
        )

    monkeypatch.setattr(service.search, "find", fake_find)
    response = await client.post(
        "/api/v1/search/search",
        json={"query": "what shipped", "mode": "context", "max_tokens": 1200},
    )

    assert response.status_code == 200
    by_uri = {entry["uri"]: entry for entry in response.json()["result"]["entries"]}
    assert "Shipped the stdio MCP proxy." in by_uri[file_uri]["text"]
    assert "MEMORY_FIELDS" not in by_uri[file_uri]["text"]
    assert by_uri[directory_uri]["detail"] == "overview"
    assert by_uri[directory_uri]["text"] == "Entities directory: projects, people and software."


async def test_context_mode_dedup_ledger_round_trips_through_agfs(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    file_uri = "viking://user/default/memories/events/repeat.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user("default"), role=Role.ROOT)
    await service.viking_fs.write_file(uri=file_uri, content="# Summary\nrepeated fact", ctx=ctx)

    async def fake_find(**kwargs):
        del kwargs
        return _FakeFindResult([_memory(file_uri, 0.7, "repeat abstract")])

    monkeypatch.setattr(service.search, "find", fake_find)
    create = await client.post("/api/v1/sessions", json={})
    session_id = create.json()["result"]["session_id"]
    payload = {
        "query": "repeat",
        "mode": "context",
        "session_id": session_id,
        "dedup_turns": 3,
        "query_expansion": "off",
    }

    first = await client.post("/api/v1/search/search", json=payload)
    assert [entry["uri"] for entry in first.json()["result"]["entries"]] == [file_uri]

    ledger_uri = f"viking://user/default/sessions/{session_id}/.recall_log.json"
    ledger = json.loads(await service.viking_fs.read_file(ledger_uri, ctx=ctx))
    assert file_uri in ledger["entries"]

    second = await client.post("/api/v1/search/search", json=payload)
    assert second.json()["result"]["entries"] == []
    assert second.json()["result"]["stats"]["dedup"]["cooled"] == 1
