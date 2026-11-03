# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Filesystem router tests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openviking.core.namespace_policy import (
    SYSTEM_MEMORY_BUILTIN_ROOT,
    SYSTEM_MEMORY_CUSTOM_ROOT,
)
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import filesystem
from openviking.service.fs_service import ListingPage
from openviking.storage.queuefs.queue_manager import get_queue_manager
from openviking.system_assets import builtin_memory_templates_dir
from openviking_cli.exceptions import InvalidURIError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_rm_preserves_memory_cleanup(monkeypatch):
    cleanup = {"status": "success", "memory_uris": ["viking://user/alice/memories/entities/a.md"]}

    async def fake_rm(uri, ctx=None, recursive=False, wait=False):
        return {
            "estimated_deleted_count": 1,
            "memory_cleanup": cleanup,
        }

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
async def test_cp_resolves_paths_and_preserves_service_result(monkeypatch):
    calls = []

    async def fake_cp(from_uri, to_uri, recursive=False, ctx=None):
        calls.append((from_uri, to_uri, recursive, ctx))
        return {
            "operation_id": "copy-1",
            "from": from_uri,
            "to": to_uri,
            "recursive": recursive,
            "semantic_root_uri": "viking://resources/archive",
            "semantic_status": "queued",
        }

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(cp=fake_cp)),
    )
    monkeypatch.setattr(
        filesystem,
        "resolve_path_variables",
        lambda uri: uri.replace("{test:resources}", "viking://resources"),
    )
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)

    response = await filesystem.cp(
        filesystem.CpRequest(
            from_uri="{test:resources}/a.md",
            to_uri="{test:resources}/archive/a.md",
            recursive=True,
        ),
        _ctx=ctx,
    )

    assert calls == [
        (
            "viking://resources/a.md",
            "viking://resources/archive/a.md",
            True,
            ctx,
        )
    ]
    assert response.result == {
        "operation_id": "copy-1",
        "from": "viking://resources/a.md",
        "to": "viking://resources/archive/a.md",
        "recursive": True,
        "semantic_root_uri": "viking://resources/archive",
        "semantic_status": "queued",
    }


def test_cp_request_defaults_to_non_recursive():
    request = filesystem.CpRequest(
        from_uri="viking://resources/a.md",
        to_uri="viking://resources/b.md",
    )

    assert request.recursive is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "source", "target", "recursive", "indexed_file"),
    [
        (
            "cp",
            "viking://system/compile/example-skills/llm-wiki/SKILL.md",
            "viking://resources/transferred-file-copy.md",
            False,
            "viking://resources/transferred-file-copy.md",
        ),
        (
            "mv",
            "viking://system/compile/example-skills/llm-wiki/SKILL.md",
            "viking://resources/transferred-file-move.md",
            False,
            "viking://resources/transferred-file-move.md",
        ),
        (
            "cp",
            "viking://system/compile/example-skills/llm-wiki",
            "viking://resources/transferred-dir-copy",
            True,
            "viking://resources/transferred-dir-copy/SKILL.md",
        ),
        (
            "mv",
            "viking://system/compile/example-skills/llm-wiki",
            "viking://resources/transferred-dir-move",
            True,
            "viking://resources/transferred-dir-move/SKILL.md",
        ),
    ],
)
async def test_file_only_transfer_builds_searchable_target_index(
    service,
    operation,
    source,
    target,
    recursive,
    indexed_file,
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)

    if operation == "cp":
        await service.fs.cp(source, target, recursive=recursive, ctx=ctx)
    else:
        await service.fs.mv(source, target, ctx=ctx)
    await asyncio.wait_for(get_queue_manager().wait_complete(), 30)

    record = await service.viking_fs.vector_store.fetch_by_uri(indexed_file, ctx=ctx)
    assert record is not None
    assert record["level"] == 2
    result = await service.search.search(
        query="Fake summary for testing",
        target_uri=target if recursive else target.rsplit("/", 1)[0],
        level=[2],
        score_threshold=0,
        ctx=ctx,
    )
    assert any(item.uri == indexed_file for item in result.resources)


@pytest.mark.asyncio
async def test_cp_canonicalizes_home_aliases(monkeypatch):
    calls = []
    calls = []

    async def fake_cp(from_uri, to_uri, recursive=False, ctx=None):
        calls.append((from_uri, to_uri, recursive, ctx))
        return {}

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(cp=fake_cp)),
    )
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)

    response = await filesystem.cp(
        filesystem.CpRequest(
            from_uri="viking://~/resources/a.md",
            to_uri="viking://~/resources/b.md",
        ),
        _ctx=ctx,
    )

    assert calls == [
        (
            "viking://user/alice/resources/a.md",
            "viking://user/alice/resources/b.md",
            False,
            ctx,
        )
    ]
    assert response.result["from"] == "viking://user/alice/resources/a.md"
    assert response.result["to"] == "viking://user/alice/resources/b.md"


@pytest.mark.asyncio
async def test_cp_rejects_invalid_uri_before_calling_service(monkeypatch):
    cp_mock = AsyncMock()
    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(cp=cp_mock)),
    )

    with pytest.raises(InvalidURIError, match="Invalid URI"):
        await filesystem.cp(
            filesystem.CpRequest(
                from_uri="/local/acct/resources/a.md",
                to_uri="viking://resources/b.md",
            ),
            _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
        )

    cp_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_attrs_returns_memory_fields_and_tags(monkeypatch):
    raw_memory = (
        "Original preference\n\n"
        "<!-- MEMORY_FIELDS\n"
        '{"memory_type": "preferences", "tags": ["ui"], "fields": {"topic": "theme"}, '
        '"resource_refs": ["viking://resources/docs/api.md"]}\n'
        "-->"
    )

    async def fake_stat(uri, ctx=None, skip_count=False):
        return {"isDir": False}

    async def fake_read(uri, ctx=None):
        return raw_memory

    class FakeVectorManager:
        async def filter(self, **kwargs):
            return [
                {
                    "uri": "viking://user/alice/memories/preferences/theme.md",
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
        "version": 1,
        "tags": ["ui"],
        "fields": {"topic": "theme"},
        "resource_refs": ["viking://resources/docs/api.md"],
        "memory_type": "preferences",
    }
    assert attrs["tags"] == ["team=search"]


@pytest.mark.asyncio
async def test_system_attrs_returns_empty_tags_without_querying_vector_store(monkeypatch):
    uri = "viking://system/memory/memory_templates/custom/demo.md"
    stat = AsyncMock(return_value={"isDir": False})
    vector_filter = AsyncMock(side_effect=AssertionError("system attrs must not query vectors"))
    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(stat=stat),
            vikingdb_manager=SimpleNamespace(filter=vector_filter),
        ),
    )
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)

    response = await filesystem.attrs(uri=uri, _ctx=ctx)

    assert response.result == {
        "uri": uri,
        "context_type": "resource",
        "attrs": {"tags": []},
    }
    stat.assert_awaited_once_with(uri, ctx=ctx, skip_count=True)
    vector_filter.assert_not_awaited()


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
    request_context = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)

    async def fake_stat(uri, ctx=None, include_lock_status=False):
        seen.update(uri=uri, ctx=ctx, include_lock_status=include_lock_status)
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
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/fs/stat",
            params={"uri": "viking://~/resources/notes.md"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["uri"] == "viking://user/alice/resources/notes.md"
    assert seen["uri"] == "viking://user/alice/resources/notes.md"
    assert seen["ctx"].user.user_id == "alice"
    assert seen["include_lock_status"] is True


@pytest.mark.asyncio
async def test_http_stat_by_record_id_returns_resolved_uri(monkeypatch):
    record_id = "0123456789abcdef0123456789abcdef"
    resolved_uri = "viking://user/alice/resources/notes.md"
    seen = {}

    async def fake_stat(uri, ctx=None, include_lock_status=False):
        seen.update(uri=uri, ctx=ctx, include_lock_status=include_lock_status)
        return {
            "uri": resolved_uri,
            "name": "notes.md",
            "size": 12,
            "mode": 0o644,
            "modTime": "2026-08-28T00:00:00Z",
            "isDir": False,
            "isLocked": False,
            "id": record_id,
        }

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(stat=fake_stat)),
    )

    app = FastAPI()
    app.include_router(filesystem.router)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("acct", "alice"), role=Role.USER
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/fs/stat", params={"uri": record_id})

    assert response.status_code == 200, response.text
    assert response.json()["result"]["uri"] == resolved_uri
    assert seen["uri"] == record_id
    assert seen["include_lock_status"] is True


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
    assert response.json()["has_more"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["ls", "tree"])
async def test_system_listing_default_http_output_uses_only_physical_metadata(
    app, client, service, endpoint
):
    root = "viking://system/memory/memory_templates/custom/http-default"
    child = f"{root}/demo"
    root_ctx = RequestContext(user=UserIdentifier("default", "default"), role=Role.ROOT)
    await service.viking_fs.mkdir(child, exist_ok=True, ctx=root_ctx)

    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("default", "alice"), role=Role.USER
    )
    try:
        response = await client.get(
            f"/api/v1/fs/{endpoint}",
            params={"uri": root},
        )
    finally:
        app.dependency_overrides.pop(get_request_context, None)

    assert response.status_code == 200, response.text
    entries = response.json()["result"]
    assert entries
    assert all("abstract" not in entry and "overview" not in entry for entry in entries)


@pytest.mark.asyncio
async def test_service_startup_syncs_builtin_assets_and_custom_directory(service):
    ctx = RequestContext(user=UserIdentifier("default", "default"), role=Role.ROOT)
    profile_uri = f"{SYSTEM_MEMORY_BUILTIN_ROOT}/profile.yaml"

    assert await service.viking_fs.read_file_bytes(
        profile_uri,
        ctx=ctx,
    ) == (builtin_memory_templates_dir() / "profile.yaml").read_bytes()
    assert (
        await service.viking_fs.stat(
            SYSTEM_MEMORY_CUSTOM_ROOT,
            ctx=ctx,
            skip_count=True,
        )
    )["isDir"]


@pytest.mark.asyncio
async def test_ls_and_tree_forward_pagination_to_filesystem_service(monkeypatch):
    seen = {"ls": {}, "tree": {}}

    async def fake_ls(uri, **kwargs):
        seen["ls"].update(uri=uri, **kwargs)
        return ListingPage(entries=[{"name": "a.md"}], has_more=True)

    async def fake_tree(uri, **kwargs):
        seen["tree"].update(uri=uri, **kwargs)
        return ListingPage(entries=[{"name": "b.md"}], has_more=False)

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(ls=fake_ls, tree=fake_tree)),
    )

    ls_response = await filesystem.ls(
        uri="viking://resources",
        tags=["team=search", "env=prod"],
        offset=4,
        limit=9,
        include_abstract=False,
        include_overview=True,
        overview_limit=512,
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )
    tree_response = await filesystem.tree(
        uri="viking://resources",
        offset=3,
        limit=5,
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    assert seen["ls"]["tags"] == ["team=search", "env=prod"]
    assert seen["ls"]["offset"] == 4
    assert seen["ls"]["node_limit"] == 9
    assert seen["ls"]["include_abstract"] is False
    assert seen["ls"]["include_overview"] is True
    assert seen["ls"]["overview_limit"] == 512
    assert seen["tree"]["offset"] == 3
    assert seen["tree"]["node_limit"] == 5
    assert ls_response.result == [{"name": "a.md"}]
    assert ls_response.has_more is True
    assert tree_response.result == [{"name": "b.md"}]
    assert tree_response.has_more is False
