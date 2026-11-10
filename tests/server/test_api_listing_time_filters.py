# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end tests for the listing time filters.

The filters have to survive the whole request path: query parameter parsing,
service dispatch, and the metadata lookup that decides the match.
"""

import httpx

from openviking.core.namespace import canonical_session_uri
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier

DEFAULT_USER = UserIdentifier.the_default_user()


async def _create_session(client: httpx.AsyncClient, session_id: str) -> str:
    created = await client.post("/api/v1/sessions", json={"session_id": session_id})
    assert created.status_code == 200, created.text
    return created.json()["result"]["session_id"]


async def test_session_listing_keeps_only_sessions_inside_the_window(client: httpx.AsyncClient):
    session_id = await _create_session(client, "time-filter-session")

    unfiltered = await client.get("/api/v1/sessions")
    assert unfiltered.status_code == 200
    assert session_id in {item["session_id"] for item in unfiltered.json()["result"]}

    inside = await client.get("/api/v1/sessions", params={"since": "2000-01-01"})
    assert inside.status_code == 200
    assert session_id in {item["session_id"] for item in inside.json()["result"]}

    outside = await client.get("/api/v1/sessions", params={"since": "2099-01-01"})
    assert outside.status_code == 200
    assert outside.json()["result"] == []


async def test_session_listing_reports_the_matched_timestamps(client: httpx.AsyncClient):
    session_id = await _create_session(client, "time-filter-timestamps")

    response = await client.get("/api/v1/sessions", params={"since": "2000-01-01"})
    assert response.status_code == 200

    matched = next(item for item in response.json()["result"] if item["session_id"] == session_id)
    assert matched["created_at"]
    assert matched["updated_at"]


async def test_session_listing_rejects_an_unknown_time_field(client: httpx.AsyncClient):
    response = await client.get(
        "/api/v1/sessions",
        params={"since": "7d", "time_field": "published_at"},
    )
    assert response.status_code == 400


async def test_session_listing_rejects_an_inverted_window(client: httpx.AsyncClient):
    response = await client.get(
        "/api/v1/sessions",
        params={"since": "2099-01-01", "until": "2000-01-01"},
    )
    assert response.status_code == 400


async def test_session_listing_paginates(client: httpx.AsyncClient):
    await _create_session(client, "time-filter-page-a")
    await _create_session(client, "time-filter-page-b")

    response = await client.get("/api/v1/sessions", params={"limit": 1})
    assert response.status_code == 200
    assert len(response.json()["result"]) == 1


async def test_directory_listing_keeps_only_entries_inside_the_window(
    client: httpx.AsyncClient,
    service,
):
    ctx = RequestContext(user=DEFAULT_USER, role=Role.ROOT)
    memories_uri = f"viking://user/{DEFAULT_USER.user_id}/memories"
    await service.viking_fs.mkdir(memories_uri, exist_ok=True, ctx=ctx)
    await service.viking_fs.write_file(
        f"{memories_uri}/time-filter-memory.md",
        "# Memory\n\nRecorded inside the window.\n",
        ctx=ctx,
    )

    unfiltered = await client.get("/api/v1/fs/ls", params={"uri": memories_uri})
    assert unfiltered.status_code == 200
    names = {entry["name"] for entry in unfiltered.json()["result"]}
    assert "time-filter-memory.md" in names

    inside = await client.get(
        "/api/v1/fs/ls",
        params={"uri": memories_uri, "since": "2000-01-01"},
    )
    assert inside.status_code == 200
    assert "time-filter-memory.md" in {entry["name"] for entry in inside.json()["result"]}

    outside = await client.get(
        "/api/v1/fs/ls",
        params={"uri": memories_uri, "since": "2099-01-01"},
    )
    assert outside.status_code == 200
    assert "time-filter-memory.md" not in {entry["name"] for entry in outside.json()["result"]}


async def test_directory_listing_rejects_an_inverted_window(client: httpx.AsyncClient):
    response = await client.get(
        "/api/v1/fs/ls",
        params={
            "uri": f"viking://user/{DEFAULT_USER.user_id}/memories",
            "since": "2099-01-01",
            "until": "2000-01-01",
        },
    )
    assert response.status_code == 400


async def test_session_directory_can_be_listed_by_time(client: httpx.AsyncClient):
    session_id = await _create_session(client, "time-filter-session-dir")
    sessions_uri = canonical_session_uri(RequestContext(user=DEFAULT_USER, role=Role.ROOT))

    unfiltered = await client.get("/api/v1/fs/ls", params={"uri": sessions_uri})
    assert unfiltered.status_code == 200
    assert session_id in {entry["name"] for entry in unfiltered.json()["result"]}

    outside = await client.get(
        "/api/v1/fs/ls",
        params={"uri": sessions_uri, "since": "2099-01-01"},
    )
    assert outside.status_code == 200
    assert session_id not in {entry["name"] for entry in outside.json()["result"]}
