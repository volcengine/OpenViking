# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import httpx

from openviking.server.identity import RequestContext, Role


def _event_tags(response: httpx.Response) -> list[str]:
    return response.json()["result"]["memory_extraction_config"]["events"]["tags"]


async def test_session_config_updates_event_tags_and_auto_commit_policy(
    client: httpx.AsyncClient,
):
    created = await client.post(
        "/api/v1/sessions",
        json={
            "memory_extraction_config": {
                "events": {
                    "tags": [
                        " Team=Search ",
                        "team=search",
                        "missing-equals",
                        "channel=obsolete",
                        "channel=web",
                    ]
                }
            },
            "auto_commit_policy": {
                "pending_token_threshold": 8000,
                "message_count_threshold": 40,
            },
        },
    )
    assert created.status_code == 200
    assert _event_tags(created) == ["team=search", "channel=web"]
    session_id = created.json()["result"]["session_id"]

    fetched = await client.get(f"/api/v1/sessions/{session_id}")
    assert _event_tags(fetched) == ["team=search", "channel=web"]
    assert "event_search_tags" not in fetched.json()["result"]

    updated = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={
            "memory_extraction_config": {"events": {"tags": ["channel=web", "channel=app"]}},
            "auto_commit_policy": {"message_count_threshold": 25},
        },
    )
    result = updated.json()["result"]
    assert updated.status_code == 200
    assert _event_tags(updated) == ["channel=app"]
    assert result["auto_commit_policy"]["pending_token_threshold"] == 8000
    assert result["auto_commit_policy"]["message_count_threshold"] == 25

    cleared = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={"memory_extraction_config": {"events": {"tags": []}}},
    )
    assert _event_tags(cleared) == []
    assert cleared.json()["result"]["auto_commit_policy"]["message_count_threshold"] == 25

    disabled = await client.patch(
        f"/api/v1/sessions/{session_id}/config",
        json={"auto_commit_policy": None},
    )
    assert disabled.status_code == 200
    assert disabled.json()["result"]["auto_commit_policy"] is None


async def test_commit_duplicate_event_tag_keys_keep_last_value(client: httpx.AsyncClient, service):
    created = await client.post(
        "/api/v1/sessions",
        json={"memory_extraction_config": {"events": {"tags": ["channel=default"]}}},
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["result"]["session_id"]
    added = await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I want a refund"},
    )
    assert added.status_code == 200, added.text

    response = await client.post(
        f"/api/v1/sessions/{session_id}/commit",
        json={"extraction_metadata": {"event": {"tags": ["channel=web", "channel=app"]}}},
    )

    assert response.status_code == 200, response.text
    archive_uri = response.json()["result"]["archive_uri"]
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    archive_meta = json.loads(
        await service.viking_fs.read_file(f"{archive_uri}/.meta.json", ctx=ctx)
    )
    assert archive_meta["phase1"]["queue_message"]["event_search_tags"] == ["channel=app"]
    session = await client.get(f"/api/v1/sessions/{session_id}")
    assert _event_tags(session) == ["channel=default"]
