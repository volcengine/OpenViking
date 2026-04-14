# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for dedicated agent content endpoints."""


async def test_agent_content_endpoint_registered(client):
    resp = await client.get("/api/v1/agent-content")
    assert resp.status_code == 405


async def test_create_agent_content_endpoint_creates_carrier(client):
    uri = "viking://agent/default/memories/patterns/distilled-project.md"
    resp = await client.post(
        "/api/v1/agent-content",
        json={
            "uri": uri,
            "content": "# Distilled Project\n\nInitial pattern notes.",
            "wait": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["mode"] == "create"
    assert body["result"]["created"] is True

    read_resp = await client.get("/api/v1/content/read", params={"uri": uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == "# Distilled Project\n\nInitial pattern notes."


async def test_agent_content_write_endpoint_appends_existing_carrier(client):
    uri = "viking://agent/default/memories/patterns/distilled-project.md"
    create_resp = await client.post(
        "/api/v1/agent-content",
        json={"uri": uri, "content": "Initial body", "wait": True},
    )
    assert create_resp.status_code == 200

    write_resp = await client.post(
        "/api/v1/agent-content/write",
        json={
            "uri": uri,
            "content": "\n\nAppended lesson.",
            "mode": "append",
            "wait": True,
        },
    )

    assert write_resp.status_code == 200
    body = write_resp.json()
    assert body["status"] == "ok"
    assert body["result"]["mode"] == "append"
    assert body["result"]["created"] is False

    read_resp = await client.get("/api/v1/content/read", params={"uri": uri})
    assert read_resp.status_code == 200
    assert read_resp.json()["result"] == "Initial body\n\nAppended lesson."


async def test_agent_content_write_endpoint_rejects_non_agent_uri(client):
    resp = await client.post(
        "/api/v1/agent-content/write",
        json={
            "uri": "viking://user/default/memories/preferences/theme.md",
            "content": "updated",
        },
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
