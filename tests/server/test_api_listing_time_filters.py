# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end tests for the listing time filters.

The filters have to survive the whole request path: query parameter parsing,
service dispatch, and the metadata lookup that decides the match.
"""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from openviking.core.namespace import canonical_session_uri
from openviking.server.identity import RequestContext, Role
from openviking.utils.time_utils import parse_iso_datetime
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


async def test_session_listing_keeps_a_session_inside_a_sub_second_window(
    client: httpx.AsyncClient,
):
    # The listing truncates the directory modification time to whole seconds
    # while the session metadata keeps the sub-second precision, so a lower
    # bound inside the same second must not drop the session.
    session_ids = [
        await _create_session(client, f"time-filter-sub-second-{index}") for index in range(5)
    ]

    listing = await client.get("/api/v1/sessions", params={"since": "2000-01-01"})
    assert listing.status_code == 200
    matched = {item["session_id"]: item for item in listing.json()["result"]}

    for session_id in session_ids:
        updated_at = parse_iso_datetime(matched[session_id]["updated_at"])
        milliseconds = updated_at.microsecond / 1000
        if not milliseconds:
            continue
        since = updated_at.replace(microsecond=int(milliseconds / 2 * 1000))
        response = await client.get(
            "/api/v1/sessions",
            params={"since": since.isoformat().replace("+00:00", "Z")},
        )
        assert response.status_code == 200
        assert session_id in {item["session_id"] for item in response.json()["result"]}
        return

    pytest.skip("no session carried a sub-second timestamp")


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

    unfiltered = await client.get(
        "/api/v1/fs/ls",
        params={"uri": memories_uri, "output": "original"},
    )
    assert unfiltered.status_code == 200
    names = {entry["name"] for entry in unfiltered.json()["result"]}
    assert "time-filter-memory.md" in names

    inside = await client.get(
        "/api/v1/fs/ls",
        params={"uri": memories_uri, "output": "original", "since": "2000-01-01"},
    )
    assert inside.status_code == 200
    assert "time-filter-memory.md" in {entry["name"] for entry in inside.json()["result"]}

    outside = await client.get(
        "/api/v1/fs/ls",
        params={"uri": memories_uri, "output": "original", "since": "2099-01-01"},
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

    unfiltered = await client.get(
        "/api/v1/fs/ls",
        params={"uri": sessions_uri, "output": "original"},
    )
    assert unfiltered.status_code == 200
    assert session_id in {entry["name"] for entry in unfiltered.json()["result"]}

    outside = await client.get(
        "/api/v1/fs/ls",
        params={"uri": sessions_uri, "output": "original", "since": "2099-01-01"},
    )
    assert outside.status_code == 200
    assert session_id not in {entry["name"] for entry in outside.json()["result"]}


def _midpoint(earlier: str, later: str) -> str:
    """Return an ISO timestamp between two listing timestamps."""
    earlier_dt = parse_iso_datetime(earlier)
    later_dt = parse_iso_datetime(later)
    middle = earlier_dt + (later_dt - earlier_dt) / 2
    return middle.astimezone(timezone.utc).isoformat(timespec="milliseconds")


async def test_directory_listing_keeps_matching_files_behind_the_directory_group(
    client: httpx.AsyncClient,
    service,
):
    """A descending listing must not stop inside the directory group.

    A listing is ordered "directories first, then files", each group sorted by
    modification time. The first raw page therefore holds directories only, and
    a scan that stops there would skip the file group further down - where a
    file that belongs inside the window can sit.
    """
    ctx = RequestContext(user=DEFAULT_USER, role=Role.ROOT)
    root_uri = f"viking://user/{DEFAULT_USER.user_id}/memories/mixed-group-desc"
    await service.viking_fs.mkdir(root_uri, exist_ok=True, ctx=ctx)

    # More directories than fit into the first raw page (256 entries).
    for index in range(260):
        await service.viking_fs.mkdir(f"{root_uri}/dir-{index:04d}", ctx=ctx)
    await asyncio.sleep(1.1)
    await service.viking_fs.write_file(
        f"{root_uri}/matching.md",
        "# Matching\n\nWritten after the directories.\n",
        ctx=ctx,
    )

    cutoff = await _cutoff_between(client, root_uri, older="directory")
    response = await client.get(
        "/api/v1/fs/ls",
        params={
            "uri": root_uri,
            "output": "original",
            "limit": 1,
            "sort_by": "mtime",
            "sort_order": "desc",
            "since": cutoff,
        },
    )

    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()["result"]}
    assert "matching.md" in names, "the file inside the window was dropped"


async def test_directory_listing_keeps_matching_files_behind_the_directory_group_ascending(
    client: httpx.AsyncClient,
    service,
):
    """An ascending listing must not stop inside the directory group either.

    The mirror image of the descending case: the directories are newer than the
    upper bound and the file is older, so the file is the one that belongs
    inside the window and it sits behind the directory group.
    """
    ctx = RequestContext(user=DEFAULT_USER, role=Role.ROOT)
    root_uri = f"viking://user/{DEFAULT_USER.user_id}/memories/mixed-group-asc"
    await service.viking_fs.mkdir(root_uri, exist_ok=True, ctx=ctx)

    await service.viking_fs.write_file(
        f"{root_uri}/matching.md",
        "# Matching\n\nWritten before the directories.\n",
        ctx=ctx,
    )
    await asyncio.sleep(1.1)
    for index in range(260):
        await service.viking_fs.mkdir(f"{root_uri}/dir-{index:04d}", ctx=ctx)

    cutoff = await _cutoff_between(client, root_uri, older="file")
    response = await client.get(
        "/api/v1/fs/ls",
        params={
            "uri": root_uri,
            "output": "original",
            "limit": 1,
            "sort_by": "mtime",
            "sort_order": "asc",
            "until": cutoff,
        },
    )

    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()["result"]}
    assert "matching.md" in names, "the file inside the window was dropped"


async def test_tag_projection_alone_does_not_change_the_node_limit(
    client: httpx.AsyncClient,
    service,
):
    """Projecting the tags must not turn a zero node limit into a match.

    A projection on its own does not filter anything, so it must not change
    what the listing returns.
    """
    ctx = RequestContext(user=DEFAULT_USER, role=Role.ROOT)
    root_uri = f"viking://user/{DEFAULT_USER.user_id}/memories/projection-limit"
    await service.viking_fs.mkdir(root_uri, exist_ok=True, ctx=ctx)
    await service.viking_fs.write_file(
        f"{root_uri}/only-file.md",
        "# Only file\n",
        ctx=ctx,
    )

    plain = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root_uri, "output": "original", "node_limit": 0},
    )
    projected = await client.get(
        "/api/v1/fs/ls",
        params={
            "uri": root_uri,
            "output": "original",
            "node_limit": 0,
            "include_tags": "true",
        },
    )

    assert plain.status_code == 200
    assert projected.status_code == 200
    assert plain.json()["result"] == []
    assert projected.json()["result"] == []


async def test_recursive_listing_keeps_matching_files_in_a_later_subtree(
    client: httpx.AsyncClient,
    service,
):
    """A recursive listing must not stop at the first entry outside the window.

    A recursive listing walks the tree depth first, with the modification time
    ordering the siblings. It is not one directory group followed by one file
    group, so an entry outside the window says nothing about the subtrees
    further down - a scan that stops there drops the entries of the later
    subtrees.
    """
    ctx = RequestContext(user=DEFAULT_USER, role=Role.ROOT)
    root_uri = f"viking://user/{DEFAULT_USER.user_id}/memories/mixed-tree-desc"
    await service.viking_fs.mkdir(root_uri, exist_ok=True, ctx=ctx)

    # A subtree that is written first, so it sits behind the newer one.
    await service.viking_fs.mkdir(f"{root_uri}/later-in-tree/sub", exist_ok=True, ctx=ctx)
    await service.viking_fs.write_file(
        f"{root_uri}/later-in-tree/sub/matching.md",
        "# Matching\n\nWritten before the bulk.\n",
        ctx=ctx,
    )
    await asyncio.sleep(1.1)

    # More entries than fit into the first raw page (256 entries).
    await service.viking_fs.mkdir(f"{root_uri}/first-in-tree", exist_ok=True, ctx=ctx)
    for index in range(260):
        await service.viking_fs.write_file(
            f"{root_uri}/first-in-tree/old-{index:04d}.md",
            f"# Old {index}\n",
            ctx=ctx,
        )
    await asyncio.sleep(1.1)

    cutoff = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    await asyncio.sleep(1.1)

    # Both writes land inside the window, the bulk of the tree does not.
    await service.viking_fs.write_file(
        f"{root_uri}/later-in-tree/sub/matching.md",
        "# Matching\n\nRewritten inside the window.\n",
        ctx=ctx,
    )
    await service.viking_fs.mkdir(f"{root_uri}/first-in-tree/recent-directory", exist_ok=True, ctx=ctx)

    response = await client.get(
        "/api/v1/fs/ls",
        params={
            "uri": root_uri,
            "recursive": "true",
            "output": "original",
            "limit": 10,
            "sort_by": "mtime",
            "sort_order": "desc",
            "since": cutoff,
        },
    )

    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()["result"]}
    assert "matching.md" in names, "the file inside the window was dropped"


async def _cutoff_between(
    client: httpx.AsyncClient,
    root_uri: str,
    *,
    older: str,
) -> str:
    """Return a timestamp between the older and the newer group of a listing.

    ``older`` names the group whose timestamps sit below the cutoff. The
    cutoff is placed between the newest timestamp of that group and the
    oldest timestamp of the other one, so the two groups land on opposite
    sides of it and only the intended one is inside the window.
    """
    listing = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root_uri, "output": "original"},
    )
    assert listing.status_code == 200
    entries = listing.json()["result"]

    file_times = [entry["modTime"] for entry in entries if not entry.get("isDir")]
    directory_times = [entry["modTime"] for entry in entries if entry.get("isDir")]
    assert file_times and directory_times

    if older == "directory":
        earlier, later = max(directory_times), min(file_times)
    else:
        earlier, later = max(file_times), min(directory_times)
    assert earlier < later, "the listing does not have two distinct timestamps"
    return _midpoint(earlier, later)
