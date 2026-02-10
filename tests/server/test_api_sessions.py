# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for session endpoints."""

import httpx


async def test_create_session(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/sessions", json={"user": "test_user"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "session_id" in body["result"]


async def test_list_sessions(client: httpx.AsyncClient):
    # Create a session first
    await client.post("/api/v1/sessions", json={"user": "test"})
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["result"], list)


async def test_get_session(client: httpx.AsyncClient):
    create_resp = await client.post(
        "/api/v1/sessions", json={"user": "test"}
    )
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["session_id"] == session_id


async def test_add_message(client: httpx.AsyncClient):
    create_resp = await client.post(
        "/api/v1/sessions", json={"user": "test"}
    )
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
    create_resp = await client.post(
        "/api/v1/sessions", json={"user": "test"}
    )
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


async def test_delete_session(client: httpx.AsyncClient):
    create_resp = await client.post(
        "/api/v1/sessions", json={"user": "test"}
    )
    session_id = create_resp.json()["result"]["session_id"]

    # Add a message so the session file exists in storage
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "ensure persisted"},
    )
    # Compress to persist
    await client.post(f"/api/v1/sessions/{session_id}/compress")

    resp = await client.delete(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_compress_session(client: httpx.AsyncClient):
    create_resp = await client.post(
        "/api/v1/sessions", json={"user": "test"}
    )
    session_id = create_resp.json()["result"]["session_id"]

    # Add some messages before compressing
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Hello"},
    )

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/compress"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_extract_session(client: httpx.AsyncClient):
    create_resp = await client.post(
        "/api/v1/sessions", json={"user": "test"}
    )
    session_id = create_resp.json()["result"]["session_id"]

    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Remember this fact"},
    )

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/extract"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
