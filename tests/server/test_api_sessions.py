# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for session endpoints."""

import httpx

from openviking.server.identity import RequestContext, Role
from openviking.telemetry import get_current_telemetry
from openviking_cli.session.user_id import UserIdentifier


async def test_create_session(client: httpx.AsyncClient):
    resp = await client.post("/api/v1/sessions", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "session_id" in body["result"]


async def test_list_sessions(client: httpx.AsyncClient):
    # Create a session first
    await client.post("/api/v1/sessions", json={})
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["result"], list)


async def test_get_session(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["session_id"] == session_id


async def test_add_message(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Hello, world!"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["message_count"] == 1


async def test_add_multiple_messages(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    # Add messages one by one; each add_message call should see
    # the accumulated count (messages are loaded from storage each time)
    resp1 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message 0"},
    )
    assert resp1.json()["result"]["message_count"] >= 1

    resp2 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message 1"},
    )
    count2 = resp2.json()["result"]["message_count"]

    resp3 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message 2"},
    )
    count3 = resp3.json()["result"]["message_count"]

    # Each add should increase the count
    assert count3 >= count2


async def test_add_message_persistence_regression(client: httpx.AsyncClient, service):
    """Regression: message payload must persist as valid parts across loads."""
    create_resp = await client.post("/api/v1/sessions", json={"user": "test"})
    session_id = create_resp.json()["result"]["session_id"]

    resp1 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message A"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["result"]["message_count"] == 1

    resp2 = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Message B"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["result"]["message_count"] == 2

    # Re-load through API path to ensure session file can be parsed back.
    get_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["result"]["message_count"] == 2

    # Verify stored message content survives load/decode.
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    assert len(session.messages) == 2
    assert session.messages[0].content == "Message A"
    assert session.messages[1].content == "Message B"


async def test_delete_session(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    # Add a message so the session file exists in storage
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "ensure persisted"},
    )
    # Compress to persist
    await client.post(f"/api/v1/sessions/{session_id}/commit")

    resp = await client.delete(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_compress_session(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    # Add some messages before committing
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Hello"},
    )

    resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "usage" not in body
    assert "telemetry" not in body


async def test_compress_session_with_telemetry(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Trace this commit"},
    )

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/commit",
        json={"telemetry": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    summary = body["telemetry"]["summary"]
    assert summary["operation"] == "session.commit"
    assert {"total", "llm", "embedding"}.issubset(summary["tokens"].keys())
    assert summary["memory"]["extracted"] is not None
    assert "extract" in summary["memory"]
    assert "semantic_nodes" not in summary
    assert "usage" not in body


async def test_compress_session_with_telemetry_includes_memory_extract_breakdown(
    client: httpx.AsyncClient, service, monkeypatch
):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]

    async def fake_commit_async(_session_id: str, _ctx):
        telemetry = get_current_telemetry()
        telemetry.set("memory.extracted", 2)
        telemetry.set("memory.extract.total.duration_ms", 321.5)
        telemetry.set("memory.extract.candidates.total", 4)
        telemetry.set("memory.extract.candidates.standard", 3)
        telemetry.set("memory.extract.candidates.tool_skill", 1)
        telemetry.set("memory.extract.created", 1)
        telemetry.set("memory.extract.merged", 1)
        telemetry.set("memory.extract.deleted", 0)
        telemetry.set("memory.extract.skipped", 2)
        telemetry.set("memory.extract.stage.prepare_inputs.duration_ms", 5.0)
        telemetry.set("memory.extract.stage.llm_extract.duration_ms", 200.0)
        telemetry.set("memory.extract.stage.normalize_candidates.duration_ms", 10.0)
        telemetry.set("memory.extract.stage.tool_skill_stats.duration_ms", 4.5)
        telemetry.set("memory.extract.stage.profile_create.duration_ms", 7.0)
        telemetry.set("memory.extract.stage.tool_skill_merge.duration_ms", 15.0)
        telemetry.set("memory.extract.stage.dedup.duration_ms", 55.0)
        telemetry.set("memory.extract.stage.create_memory.duration_ms", 12.0)
        telemetry.set("memory.extract.stage.merge_existing.duration_ms", 9.0)
        telemetry.set("memory.extract.stage.delete_existing.duration_ms", 0.0)
        telemetry.set("memory.extract.stage.create_relations.duration_ms", 3.0)
        telemetry.set("memory.extract.stage.flush_semantic.duration_ms", 1.0)
        return {
            "session_id": _session_id,
            "status": "committed",
            "memories_extracted": 2,
            "active_count_updated": 0,
            "archived": True,
            "stats": None,
        }

    monkeypatch.setattr(service.sessions, "commit_async", fake_commit_async)

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/commit",
        json={"telemetry": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    extract = body["telemetry"]["summary"]["memory"]["extract"]
    assert extract["duration_ms"] == 321.5
    assert extract["candidates"] == {"total": 4, "standard": 3, "tool_skill": 1}
    assert extract["actions"] == {"created": 1, "merged": 1, "deleted": 0, "skipped": 2}
    assert extract["stages"]["llm_extract_ms"] == 200.0
    assert extract["stages"]["flush_semantic_ms"] == 1.0


async def test_compress_session_with_summary_only_telemetry(client: httpx.AsyncClient):
    create_resp = await client.post("/api/v1/sessions", json={})
    session_id = create_resp.json()["result"]["session_id"]
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Summary only telemetry"},
    )

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/commit",
        json={"telemetry": {"summary": True}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["telemetry"]["summary"]["operation"] == "session.commit"
    assert "usage" not in body
    assert "events" not in body["telemetry"]
    assert "truncated" not in body["telemetry"]
    assert "dropped" not in body["telemetry"]


async def test_extract_session_jsonable_regression(client: httpx.AsyncClient, service, monkeypatch):
    """Regression: extract endpoint should serialize internal objects."""

    class FakeMemory:
        __slots__ = ("uri",)

        def __init__(self, uri: str):
            self.uri = uri

        def to_dict(self):
            return {"uri": self.uri}

    async def fake_extract(_session_id: str, _ctx):
        return [FakeMemory("viking://user/memories/mock.md")]

    monkeypatch.setattr(service.sessions, "extract", fake_extract)

    create_resp = await client.post("/api/v1/sessions", json={"user": "test"})
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.post(f"/api/v1/sessions/{session_id}/extract")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == [{"uri": "viking://user/memories/mock.md"}]
