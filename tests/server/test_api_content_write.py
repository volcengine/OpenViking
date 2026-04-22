# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for content write endpoint."""

import pytest


async def _first_file_uri(client, root_uri: str) -> str:
    resp = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root_uri, "simple": True, "recursive": True, "output": "original"},
    )
    assert resp.status_code == 200
    children = resp.json().get("result", [])
    assert children
    return children[0]


async def test_write_endpoint_registered(client):
    resp = await client.get("/api/v1/content/write")
    assert resp.status_code == 405


async def test_write_rejects_directory_uri(client_with_resource):
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/content/write",
        json={"uri": uri, "content": "new content"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_write_rejects_derived_file_uri(client_with_resource):
    client, uri = client_with_resource
    resp = await client.post(
        "/api/v1/content/write",
        json={"uri": f"{uri}/.overview.md", "content": "new content"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_write_replaces_existing_resource_file(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)

    write_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "# Updated\n\nFresh content.",
            "mode": "replace",
            "wait": True,
        },
    )
    assert write_resp.status_code == 200
    body = write_resp.json()
    assert body["status"] == "ok"
    assert body["result"]["uri"] == file_uri
    assert body["result"]["mode"] == "replace"

    read_resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == "# Updated\n\nFresh content."


async def test_write_appends_existing_resource_file(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)
    original = (await client.get("/api/v1/content/read", params={"uri": file_uri})).json()["result"]

    write_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "\n\nAppended section.",
            "mode": "append",
            "wait": True,
        },
    )
    assert write_resp.status_code == 200

    read_resp = await client.get("/api/v1/content/read", params={"uri": file_uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == original + "\n\nAppended section."


@pytest.mark.asyncio
async def test_write_missing_uri_validation(client):
    resp = await client.post("/api/v1/content/write", json={"content": "missing uri"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_write_rejects_removed_semantic_flags(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_file_uri(client, uri)

    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": file_uri,
            "content": "updated",
            "regenerate_semantics": False,
            "revectorize": False,
        },
    )

    assert resp.status_code == 422


# --- Memory creation via /content/write ---
#
# Memory URIs (viking://<scope>/<owner>/memories/...) can be created through
# /content/write when the target file does not yet exist. Parent directories
# are auto-created and the file is indexed via the standard memory-refresh
# path, so the new memory is immediately discoverable via semantic retrieval.


async def test_write_creates_new_memory_with_generated_filename(client):
    uri = "viking://user/alice/memories/preferences/mem_pref_tabs.md"
    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "Alice prefers tabs over spaces in Python.",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["uri"] == uri
    assert result["context_type"] == "memory"
    assert result["created"] is True
    assert result["mode"] == "replace"

    read_resp = await client.get("/api/v1/content/read", params={"uri": uri})
    assert read_resp.status_code == 200
    assert "tabs over spaces" in read_resp.json()["result"]


async def test_write_creates_agent_scoped_memory(client):
    uri = "viking://agent/main/memories/tools/custom_tool.md"
    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "Custom tool guidance.",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["uri"] == uri
    assert result["created"] is True


async def test_write_creates_profile_singleton(client):
    uri = "viking://user/bob/memories/profile.md"
    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "Bob is based in Seattle.",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["uri"] == uri
    assert result["created"] is True


async def test_write_memory_append_after_create(client):
    uri = "viking://user/carol/memories/events/meeting_notes.md"
    create_resp = await client.post(
        "/api/v1/content/write",
        json={"uri": uri, "content": "Initial entry.\n", "wait": True},
    )
    assert create_resp.status_code == 200
    assert create_resp.json()["result"]["created"] is True

    append_resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
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


async def test_write_memory_append_on_missing_downgrades_to_replace(client):
    uri = "viking://user/dan/memories/events/fresh.md"
    resp = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "Only entry.\n",
            "mode": "append",
            "wait": True,
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["created"] is True
    # Append to a missing file is downgraded to replace so the call succeeds.
    assert result["mode"] == "replace"


async def test_write_memory_replace_overwrites(client):
    uri = "viking://user/dave/memories/preferences/coffee.md"
    first = await client.post(
        "/api/v1/content/write",
        json={"uri": uri, "content": "Dave drinks coffee black.", "wait": True},
    )
    assert first.status_code == 200
    assert first.json()["result"]["created"] is True

    second = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "Dave drinks coffee with oat milk now.",
            "mode": "replace",
            "wait": True,
        },
    )
    assert second.status_code == 200
    assert second.json()["result"]["created"] is False

    read_resp = await client.get("/api/v1/content/read", params={"uri": uri})
    text = read_resp.json()["result"]
    assert "oat milk" in text
    assert "black" not in text


async def test_write_memory_preserves_content_verbatim(client):
    fact = (
        "OpenViking vlm.max_concurrent set to 50 (not 100) after a /qa burst "
        "matrix showed 12% extraction loss at c=100 under saturation."
    )
    uri = "viking://agent/main/memories/cases/vlm_saturation.md"
    resp = await client.post(
        "/api/v1/content/write",
        json={"uri": uri, "content": fact, "wait": True},
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["uri"] == uri
    assert result["created"] is True

    read_resp = await client.get("/api/v1/content/read", params={"uri": uri})
    # Verbatim preservation: no extraction, no rephrasing.
    assert read_resp.json()["result"] == fact
