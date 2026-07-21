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


async def test_ls_sorts_by_mtime_before_applying_node_limit(client, service):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    root_uri = "viking://resources/mtime-before-limit"
    await service.viking_fs.mkdir(root_uri, exist_ok=True, ctx=ctx)

    for index in range(200):
        await service.viking_fs.mkdir(f"{root_uri}/a-{index:03d}", ctx=ctx)

    newest_uri = f"{root_uri}/zz-newest"
    await service.viking_fs.mkdir(newest_uri, ctx=ctx)
    await service.viking_fs.mkdir(f"{newest_uri}/activity", ctx=ctx)
    await service.viking_fs.write_file(f"{root_uri}/newer-file.md", "newer", ctx=ctx)

    response = await client.get(
        "/api/v1/fs/ls",
        params={
            "uri": root_uri,
            "output": "original",
            "node_limit": 200,
            "sort_by": "mtime",
            "sort_order": "desc",
        },
    )

    assert response.status_code == 200
    entries = response.json()["result"]
    assert len(entries) == 200
    assert entries[0]["name"] == "zz-newest"


async def test_session_list_keeps_newest_directory_past_storage_limit(client, service):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    root_uri = canonical_session_uri(ctx)
    await service.viking_fs.mkdir(root_uri, exist_ok=True, ctx=ctx)

    for index in range(1000):
        await service.viking_fs.mkdir(f"{root_uri}/a-{index:04d}", ctx=ctx)

    newest_uri = f"{root_uri}/zz-newest"
    await service.viking_fs.mkdir(newest_uri, ctx=ctx)
    await service.viking_fs.mkdir(f"{newest_uri}/activity", ctx=ctx)

    response = await client.get("/api/v1/sessions")

    assert response.status_code == 200
    sessions = response.json()["result"]
    assert len(sessions) == 1000
    assert sessions[0]["session_id"] == "zz-newest"
