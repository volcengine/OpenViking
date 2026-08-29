# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Filesystem router tests."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import filesystem
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_rm_preserves_memory_cleanup(monkeypatch):
    cleanup = {"status": "success", "memory_uris": ["viking://user/alice/memories/entities/a.md"]}

    async def fake_rm(uri, ctx=None, recursive=False, wait=False, timeout=None):
        return {"estimated_deleted_count": 1, "memory_cleanup": cleanup}

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(rm=fake_rm)),
    )

    response = await filesystem.rm(
        uri="viking://resources/id_card.pdf",
        recursive=True,
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    assert response.result["uri"] == "viking://resources/id_card.pdf"
    assert response.result["estimated_deleted_count"] == 1
    assert response.result["memory_cleanup"] == cleanup


@pytest.mark.asyncio
async def test_attrs_returns_memory_fields_and_tags(monkeypatch):
    raw_memory = (
        "Original preference\n\n"
        "<!-- MEMORY_FIELDS\n"
        '{"memory_type": "preferences", "tags": ["ui"], "fields": {"topic": "theme"}, '
        '"resource_refs": ["viking://resources/docs/api.md"]}\n'
        "-->"
    )

    async def fake_stat(uri, ctx=None):
        return {"isDir": False}

    async def fake_read(uri, ctx=None):
        return raw_memory

    class FakeVectorManager:
        async def filter(self, **kwargs):
            return [
                {
                    "uri": kwargs["filter"]["conds"][0],
                    "level": 2,
                    "search_tags": ["team=search"],
                }
            ]

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(stat=fake_stat, read=fake_read),
            vikingdb_manager=FakeVectorManager(),
        ),
    )

    response = await filesystem.attrs(
        uri="viking://user/alice/memories/preferences/theme.md",
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    attrs = response.result["attrs"]
    assert attrs["memory"] == {
        "tags": ["ui"],
        "fields": {"topic": "theme"},
        "resource_refs": ["viking://resources/docs/api.md"],
        "memory_type": "preferences",
    }
    assert attrs["tags"] == ["team=search"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN, Role.ROOT])
async def test_mkdir_echoes_canonical_home_alias(monkeypatch, role):
    seen = {}

    async def fake_mkdir(uri, ctx=None, description=None):
        seen["uri"] = uri

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(mkdir=fake_mkdir)),
    )

    response = await filesystem.mkdir(
        filesystem.MkdirRequest(uri="viking://~/resources/notes"),
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=role),
    )

    assert seen["uri"] == "viking://user/alice/resources/notes"
    assert response.result["uri"] == "viking://user/alice/resources/notes"


@pytest.mark.asyncio
async def test_dev_root_http_mkdir_expands_home_alias_from_request_identity(
    app, client, monkeypatch
):
    """Exercise the REST auth dependency and router with a dev-mode ROOT request."""
    seen = {}

    async def fake_mkdir(uri, ctx=None, description=None):
        seen.update(uri=uri, ctx=ctx, description=description)

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(mkdir=fake_mkdir)),
    )

    response = await client.post(
        "/api/v1/fs/mkdir",
        json={"uri": "viking://~/resources/notes", "description": "private notes"},
        headers={
            "X-OpenViking-Account": "acct",
            "X-OpenViking-User": "alice",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["uri"] == "viking://user/alice/resources/notes"
    assert seen["uri"] == "viking://user/alice/resources/notes"
    assert seen["ctx"].role == Role.ROOT
    assert seen["ctx"].account_id == "acct"
    assert seen["ctx"].user.user_id == "alice"


@pytest.mark.asyncio
async def test_http_stat_returns_canonical_request_uri(monkeypatch):
    seen = {}
    request_context = RequestContext(
        user=UserIdentifier("acct", "alice"), role=Role.USER
    )

    async def fake_stat(uri, ctx=None):
        seen.update(uri=uri, ctx=ctx)
        return {
            "name": "notes.md",
            "size": 12,
            "mode": 0o644,
            "modTime": "2026-08-28T00:00:00Z",
            "isDir": False,
            "isLocked": False,
        }

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(stat=fake_stat)),
    )

    app = FastAPI()
    app.include_router(filesystem.router)
    app.dependency_overrides[get_request_context] = lambda: request_context
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/fs/stat",
            params={"uri": "viking://~/resources/notes.md"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["uri"] == "viking://user/alice/resources/notes.md"
    assert seen["uri"] == "viking://user/alice/resources/notes.md"
    assert seen["ctx"].user.user_id == "alice"


@pytest.mark.asyncio
async def test_ls_user_container_lists_only_caller_space(app, client, service):
    """`viking://user` is the container of user spaces, not a current-user shorthand."""
    from openviking.server.auth import get_request_context

    root_ctx = RequestContext(user=UserIdentifier("default", "default"), role=Role.ROOT)
    await service.viking_fs.mkdir("viking://user/alice", exist_ok=True, ctx=root_ctx)
    await service.viking_fs.mkdir("viking://user/bob", exist_ok=True, ctx=root_ctx)

    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("default", "alice"), role=Role.USER
    )
    try:
        response = await client.get(
            "/api/v1/fs/ls",
            params={"uri": "viking://user", "output": "original"},
        )
    finally:
        app.dependency_overrides.pop(get_request_context, None)

    assert response.status_code == 200, response.text
    names = {entry["name"] for entry in response.json()["result"]}
    assert names == {"alice"}
