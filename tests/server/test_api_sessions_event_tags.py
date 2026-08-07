# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""HTTP tests for event-memory default tag settings on sessions.

Covers create-time defaults, GET echo, PATCH /config edits, and
commit-time override via extraction_metadata.event.tags.
"""

import httpx


def _events_tags(result: dict) -> list:
    return result["memory_extraction_config"]["events"]["tags"]


async def test_create_session_with_event_tags(client: httpx.AsyncClient):
    resp = await client.post(
        "/api/v1/sessions",
        json={
            "memory_extraction_config": {
                "events": {"tags": ["team=search", "channel=web"]}
            }
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert _events_tags(result) == ["team=search", "channel=web"]
    assert "config" not in result
    assert "event_search_tags" not in result


async def test_get_session_echoes_event_tags(client: httpx.AsyncClient):
    create = await client.post(
        "/api/v1/sessions",
        json={
            "memory_extraction_config": {"events": {"tags": ["topic=refund"]}}
        },
    )
    session_id = create.json()["result"]["session_id"]

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert _events_tags(result) == ["topic=refund"]
    assert "config" not in result
    assert "event_search_tags" not in result


async def test_get_session_without_event_tags_defaults_empty(client: httpx.AsyncClient):
    create = await client.post("/api/v1/sessions", json={})
    session_id = create.json()["result"]["session_id"]

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    result = resp.json()["result"]
    assert _events_tags(result) == []
    assert "config" not in result
    assert "event_search_tags" not in result


async def test_patch_session_config_updates_event_tags(client: httpx.AsyncClient):
    create = await client.post(
        "/api/v1/sessions",
        json={
            "memory_extraction_config": {"events": {"tags": ["channel=web"]}}
        },
    )
    session_id = create.json()["result"]["session_id"]

    patch = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={
            "memory_extraction_config": {
                "events": {"tags": ["team=search", "channel=app"]}
            }
        },
    )
    assert patch.status_code == 200
    assert _events_tags(patch.json()["result"]) == ["team=search", "channel=app"]
    assert "config" not in patch.json()["result"]

    resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert _events_tags(resp.json()["result"]) == ["team=search", "channel=app"]


async def test_patch_session_config_can_clear_event_tags(client: httpx.AsyncClient):
    create = await client.post(
        "/api/v1/sessions",
        json={
            "memory_extraction_config": {"events": {"tags": ["channel=web"]}}
        },
    )
    session_id = create.json()["result"]["session_id"]

    patch = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={"memory_extraction_config": {"events": {"tags": []}}},
    )
    assert patch.status_code == 200
    assert _events_tags(patch.json()["result"]) == []


async def test_patch_session_config_merges_auto_commit_policy(client: httpx.AsyncClient):
    create = await client.post(
        "/api/v1/sessions",
        json={
            "auto_commit_policy": {
                "pending_token_threshold": 8000,
                "message_count_threshold": 40,
                "keep_recent_count": 10,
            }
        },
    )
    session_id = create.json()["result"]["session_id"]

    patch = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={"auto_commit_policy": {"message_count_threshold": 25}},
    )

    assert patch.status_code == 200
    assert patch.json()["result"]["auto_commit_policy"] == {
        "pending_token_threshold": 8000,
        "message_count_threshold": 25,
        "idle_timeout_seconds": 86400,
        "keep_recent_count": 10,
        "min_commit_interval_seconds": 0,
    }


async def test_patch_session_config_can_enable_and_disable_auto_commit(
    client: httpx.AsyncClient,
):
    create = await client.post("/api/v1/sessions", json={})
    session_id = create.json()["result"]["session_id"]

    enabled = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={"auto_commit_policy": {"message_count_threshold": 25}},
    )
    assert enabled.status_code == 200
    assert enabled.json()["result"]["auto_commit_policy"] == {
        "pending_token_threshold": 10000,
        "message_count_threshold": 25,
        "idle_timeout_seconds": 86400,
        "keep_recent_count": 2,
        "min_commit_interval_seconds": 0,
    }

    disabled = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={"auto_commit_policy": None},
    )
    assert disabled.status_code == 200
    assert disabled.json()["result"]["auto_commit_policy"] is None


async def test_patch_session_config_updates_policy_and_tags_together(
    client: httpx.AsyncClient,
):
    create = await client.post("/api/v1/sessions", json={})
    session_id = create.json()["result"]["session_id"]

    patch = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={
            "auto_commit_policy": {"pending_token_threshold": 7000},
            "memory_extraction_config": {
                "events": {"tags": ["team=search", "channel=app"]}
            },
        },
    )

    assert patch.status_code == 200
    result = patch.json()["result"]
    assert result["auto_commit_policy"]["pending_token_threshold"] == 7000
    assert _events_tags(result) == ["team=search", "channel=app"]


async def test_patch_session_config_omitted_policy_is_unchanged(
    client: httpx.AsyncClient,
):
    create = await client.post(
        "/api/v1/sessions",
        json={
            "auto_commit_policy": {
                "pending_token_threshold": 8000,
                "message_count_threshold": 40,
            }
        },
    )
    session_id = create.json()["result"]["session_id"]

    patch = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={
            "memory_extraction_config": {
                "events": {"tags": ["channel=app"]}
            }
        },
    )

    assert patch.status_code == 200
    assert patch.json()["result"]["auto_commit_policy"] == {
        "pending_token_threshold": 8000,
        "message_count_threshold": 40,
        "idle_timeout_seconds": 86400,
        "keep_recent_count": 2,
        "min_commit_interval_seconds": 0,
    }


async def test_commit_accepts_event_tags_override(client: httpx.AsyncClient):
    create = await client.post("/api/v1/sessions", json={})
    session_id = create.json()["result"]["session_id"]
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I want a refund"},
    )

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/commit",
        json={"extraction_metadata": {"event": {"tags": ["team=search"]}}},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
