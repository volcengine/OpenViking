# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for POST /api/v1/memories (direct memory creation, no extraction)."""


async def test_create_memory_with_generated_filename(client):
    resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "alice",
            "bucket": "preferences",
            "content": "Alice prefers tabs over spaces in Python.",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["uri"].startswith("viking://user/alice/memories/preferences/mem_")
    assert result["uri"].endswith(".md")
    assert result["context_type"] == "memory"
    assert result["created"] is True
    assert result["mode"] == "replace"

    read_resp = await client.get("/api/v1/content/read", params={"uri": result["uri"]})
    assert read_resp.status_code == 200
    assert "tabs over spaces" in read_resp.json()["result"]


async def test_create_memory_with_stable_filename(client):
    resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "agent",
            "owner_id": "main",
            "bucket": "tools",
            "filename": "custom_tool",
            "content": "Custom tool guidance.",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["uri"] == "viking://agent/main/memories/tools/custom_tool.md"
    assert result["created"] is True


async def test_create_memory_profile_singleton(client):
    resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "bob",
            "bucket": "profile",
            "content": "Bob is based in Seattle.",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["uri"] == "viking://user/bob/memories/profile.md"
    assert result["created"] is True


async def test_create_memory_profile_rejects_filename(client):
    resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "bob",
            "bucket": "profile",
            "filename": "custom",
            "content": "Some content.",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_create_memory_append_mode(client):
    create_resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "carol",
            "bucket": "events",
            "filename": "meeting_notes",
            "content": "Initial entry.\n",
            "wait": True,
        },
    )
    assert create_resp.status_code == 200
    uri = create_resp.json()["result"]["uri"]

    append_resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "carol",
            "bucket": "events",
            "filename": "meeting_notes",
            "content": "Appended entry.\n",
            "mode": "append",
            "wait": True,
        },
    )
    assert append_resp.status_code == 200
    append_result = append_resp.json()["result"]
    assert append_result["uri"] == uri
    assert append_result["created"] is False
    assert append_result["mode"] == "append"

    read_resp = await client.get("/api/v1/content/read", params={"uri": uri})
    text = read_resp.json()["result"]
    assert "Initial entry." in text
    assert "Appended entry." in text


async def test_create_memory_replace_overwrites(client):
    first = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "dave",
            "bucket": "preferences",
            "filename": "coffee",
            "content": "Dave drinks coffee black.",
            "wait": True,
        },
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "dave",
            "bucket": "preferences",
            "filename": "coffee",
            "content": "Dave drinks coffee with oat milk now.",
            "mode": "replace",
            "wait": True,
        },
    )
    assert second.status_code == 200
    assert second.json()["result"]["created"] is False

    read_resp = await client.get(
        "/api/v1/content/read",
        params={"uri": "viking://user/dave/memories/preferences/coffee.md"},
    )
    text = read_resp.json()["result"]
    assert "oat milk" in text
    assert "black" not in text


async def test_create_memory_rejects_invalid_bucket(client):
    resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "alice",
            "bucket": "journal",
            "content": "Hello.",
        },
    )
    assert resp.status_code == 422


async def test_create_memory_rejects_invalid_scope(client):
    resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "resources",
            "owner_id": "alice",
            "bucket": "preferences",
            "content": "Hello.",
        },
    )
    assert resp.status_code == 422


async def test_create_memory_rejects_path_traversal_in_filename(client):
    resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "alice",
            "bucket": "preferences",
            "filename": "../escape",
            "content": "Nope.",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_create_memory_rejects_path_traversal_in_owner(client):
    resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "user",
            "owner_id": "../alice",
            "bucket": "preferences",
            "content": "Nope.",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_create_memory_preserves_content_verbatim(client):
    fact = (
        "OpenViking vlm.max_concurrent set to 50 (not 100) after a /qa burst "
        "matrix showed 12% extraction loss at c=100 under saturation."
    )
    resp = await client.post(
        "/api/v1/memories",
        json={
            "scope": "agent",
            "owner_id": "main",
            "bucket": "cases",
            "content": fact,
            "wait": True,
        },
    )
    assert resp.status_code == 200
    uri = resp.json()["result"]["uri"]
    read_resp = await client.get("/api/v1/content/read", params={"uri": uri})
    # Verbatim preservation: no extraction, no rephrasing.
    assert read_resp.json()["result"] == fact
