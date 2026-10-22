# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""End-to-end coverage for ordered, limited filesystem listings."""

from types import SimpleNamespace

import pytest

from openviking.core.namespace import canonical_session_uri
from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking.service.session_service import SessionService
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.fixture
def service(temp_dir):
    mock_agfs = MockLocalAGFS(root_path=temp_dir / "mock_agfs_root")
    viking_fs = VikingFS(agfs=mock_agfs)
    return SimpleNamespace(
        fs=FSService(viking_fs=viking_fs),
        sessions=SessionService(viking_fs=viking_fs),
        viking_fs=viking_fs,
    )


async def test_ls_sorts_before_applying_offset_and_limit(client, service):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    root_uri = "viking://resources/mtime-before-limit"
    await service.viking_fs.mkdir(root_uri, exist_ok=True, ctx=ctx)

    for index in range(300):
        await service.viking_fs.mkdir(f"{root_uri}/a-{index:03d}", ctx=ctx)

    newest_uri = f"{root_uri}/zz-newest"
    await service.viking_fs.mkdir(newest_uri, ctx=ctx)
    await service.viking_fs.mkdir(f"{root_uri}/tasks", ctx=ctx)
    await service.viking_fs.mkdir(f"{newest_uri}/activity", ctx=ctx)
    await service.viking_fs.write_file(f"{root_uri}/newer-file.md", "newer", ctx=ctx)

    mtime_response = await client.get(
        "/api/v1/fs/ls",
        params={
            "uri": root_uri,
            "output": "original",
            "limit": 200,
            "sort_by": "mtime",
            "sort_order": "desc",
        },
    )
    response = await client.get(
        "/api/v1/fs/ls",
        params={
            "uri": root_uri,
            "output": "original",
            "offset": 1,
            "limit": 2,
            "sort_by": "name",
            "sort_order": "desc",
        },
    )

    assert mtime_response.status_code == 200
    assert mtime_response.json()["result"]["items"][0]["name"] == "zz-newest"
    assert response.status_code == 200
    entries = response.json()["result"]["items"]
    assert [entry["name"] for entry in entries] == ["tasks", "a-299"]

    filtered_page = await client.get(
        "/api/v1/fs/ls",
        params={
            "uri": root_uri,
            "output": "original",
            "limit": 256,
            "sort_by": "name",
            "sort_order": "desc",
        },
    )
    filtered_entries = filtered_page.json()["result"]["items"]
    assert len(filtered_entries) == 256
    # A user directory named "tasks" below the account root is ordinary content.
    assert any(entry["name"] == "tasks" for entry in filtered_entries)

    invalid_offset = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root_uri, "offset": -1},
    )
    invalid_limit = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root_uri, "limit": 0},
    )
    legacy_limit = await client.get(
        "/api/v1/fs/ls",
        params={"uri": root_uri, "node_limit": 2},
    )
    assert invalid_offset.status_code == 400
    assert invalid_limit.status_code == 400
    assert legacy_limit.status_code == 400


async def test_session_list_pages_all_directories_in_stable_order(client, service):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    root_uri = canonical_session_uri(ctx)
    await service.viking_fs.mkdir(root_uri, exist_ok=True, ctx=ctx)

    for index in range(1000):
        await service.viking_fs.mkdir(f"{root_uri}/a-{index:04d}", ctx=ctx)

    newest_uri = f"{root_uri}/zz-newest"
    await service.viking_fs.mkdir(newest_uri, ctx=ctx)
    await service.viking_fs.mkdir(f"{newest_uri}/activity", ctx=ctx)

    offset = 0
    session_ids = []
    while True:
        response = await client.get(
            "/api/v1/sessions",
            params={"offset": offset, "limit": 50},
        )
        assert response.status_code == 200
        page = response.json()["result"]
        assert page["offset"] == offset
        assert page["limit"] == 50
        assert page["total"] is None
        session_ids.extend(item["session_id"] for item in page["items"])
        if not page["has_more"]:
            break
        offset += len(page["items"])

    assert len(session_ids) == 1001
    assert len(set(session_ids)) == 1001
    assert session_ids[0] == "zz-newest"
