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
from openviking.service.fs_service import FSService, ListingPage
from openviking.storage.queuefs.queue_manager import get_queue_manager
from openviking.system_assets import builtin_memory_templates_dir
from openviking_cli.exceptions import InvalidURIError, PermissionDeniedError
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

    fs = FSService(vikingdb=FakeVectorManager())
    monkeypatch.setattr(fs, "stat", fake_stat)
    monkeypatch.setattr(fs, "read", fake_read)
    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=fs),
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
    fs = FSService(vikingdb=SimpleNamespace(filter=vector_filter))
    monkeypatch.setattr(fs, "stat", stat)
    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=fs),
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


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"uris": []},
        {"uris": ["viking://resources/a.md"] * 51},
        {"uris": None},
        {"uris": ["viking://resources/a.md"], "unexpected": True},
    ],
)
async def test_attrs_batch_rejects_invalid_request_http(client, body):
    response = await client.post("/api/v1/fs/attrs/batch", json=body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_attrs_reuses_stat_permission_check_before_querying_tags(monkeypatch):
    uri = "viking://resources/private"
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)
    vector_filter = AsyncMock()
    stat = AsyncMock(side_effect=PermissionDeniedError("read denied"))
    fs = FSService(vikingdb=SimpleNamespace(filter=vector_filter))
    monkeypatch.setattr(fs, "stat", stat)
    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=fs),
    )
    response = await filesystem.attrs_batch(
        request=filesystem.GetAttrsRequest(uris=[uri]), _ctx=ctx
    )
    result = response.result
    assert result["items"] == []
    assert result["failed"][0]["error"]["code"] == "PERMISSION_DENIED"
    stat.assert_awaited_once_with(uri, ctx=ctx, skip_count=True)
    vector_filter.assert_not_awaited()


@pytest.mark.asyncio
async def test_attrs_batch_reuses_bounded_execution_and_preserves_failures(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)
    root = "viking://user/alice/resources/demo"
    uris = [f"{root}/{index}.md" for index in range(50)]
    release = asyncio.Event()
    active = peak = 0

    async def read(uri, ctx):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 8:
            release.set()
        await release.wait()
        active -= 1
        if uri == uris[3]:
            raise PermissionDeniedError("denied")
        return {"uri": uri, "attrs": {"tags": []}}

    fs = FSService()
    monkeypatch.setattr(filesystem, "get_service", lambda: SimpleNamespace(fs=fs))
    monkeypatch.setattr(fs, "attrs", read)
    response = await asyncio.wait_for(
        filesystem.attrs_batch(request=filesystem.GetAttrsRequest(uris=uris), _ctx=ctx),
        timeout=5,
    )
    result = response.result
    assert peak == 8
    assert [item["uri"] for item in result["items"]] == [uri for uri in uris if uri != uris[3]]
    assert result["failed"] == [
        {"uri": uris[3], "error": {"code": "PERMISSION_DENIED", "message": "denied"}}
    ]


@pytest.mark.asyncio
async def test_attrs_batch_propagates_cancellation(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)
    fs = FSService()
    monkeypatch.setattr(filesystem, "get_service", lambda: SimpleNamespace(fs=fs))
    monkeypatch.setattr(fs, "attrs", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await filesystem.attrs_batch(
            request=filesystem.GetAttrsRequest(uris=["viking://resources/a.md"]),
            _ctx=ctx,
        )


async def test_attrs_batch_http_reads_exact_levels_and_preserves_stored_usage(client, service):
    from openviking.core.namespace import canonical_user_root
    from openviking.storage.vector_ids import vector_record_id
    from openviking.utils.time_utils import get_current_timestamp

    ctx = RequestContext(user=service.user, role=Role.ROOT)
    root = f"{canonical_user_root(ctx)}/resources/attrs-test"
    file_uri = f"{root}/a.md"
    unindexed_uri = f"{root}/unindexed.md"
    missing_uri = f"{root}/missing.md"
    await service.viking_fs.mkdir(root, ctx=ctx)
    await service.viking_fs.write_file(file_uri, "original content", ctx=ctx)
    await service.viking_fs.write_file(unindexed_uri, "no index", ctx=ctx)
    timestamp = get_current_timestamp()
    seeds = [
        (root, 1, ["team=search", "used_count=7", "last_used_at=123"]),
        (root, 0, ["team=search", "used_count=5", "last_used_at=123"]),
        (root, 2, ["wrong_level=yes"]),
        (file_uri, 2, ["used_count=9", "last_used_at=456"]),
        (file_uri, 0, ["wrong_level=yes"]),
        (f"{root}/child", 0, ["descendant=yes"]),
    ]
    record_ids = []
    for uri, level, tags in seeds:
        record_id = vector_record_id(ctx.account_id, uri, level)
        record_ids.append(record_id)
        await service.vikingdb_manager.upsert(
            {
                "id": record_id,
                "uri": uri,
                "context_type": "resource",
                "level": level,
                "abstract": "original content",
                "created_at": timestamp,
                "updated_at": timestamp,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
                "search_tags": tags,
            },
            ctx=ctx,
        )
    before = await service.vikingdb_manager.get_strict(record_ids, ctx=ctx)
    headers = {
        "X-OpenViking-Account": ctx.account_id,
        "X-OpenViking-User": ctx.user.user_id,
    }
    response = await client.post(
        "/api/v1/fs/attrs/batch",
        headers=headers,
        json={
            "uris": [
                file_uri,
                root,
                "viking://~/resources/attrs-test/a.md",
                missing_uri,
                unindexed_uri,
                "invalid",
            ]
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert [item["uri"] for item in result["items"]] == [file_uri, root, unindexed_uri]
    assert result["items"][0]["attrs"]["tags"] == ["used_count=9", "last_used_at=456"]
    assert result["items"][1]["attrs"]["tags"] == [
        "team=search",
        "used_count=5",
        "last_used_at=123",
        "used_count=7",
    ]
    assert result["items"][2]["attrs"]["tags"] == []
    assert [item["uri"] for item in result["failed"]] == [missing_uri, "invalid"]
    assert [item["error"]["code"] for item in result["failed"]] == ["NOT_FOUND", "INVALID_URI"]
    for item in result["items"]:
        single = await client.get("/api/v1/fs/attrs", params={"uri": item["uri"]}, headers=headers)
        assert single.status_code == 200, single.text
        assert single.json()["result"] == item
    assert await service.vikingdb_manager.get_strict(record_ids, ctx=ctx) == before
    assert await service.viking_fs.read_file(file_uri, ctx=ctx) == "original content"


async def test_attrs_batch_denies_foreign_memory_and_keeps_own_memory(service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.USER)
    own_uri = f"viking://user/{ctx.user.user_id}/memories/preferences/theme.md"
    foreign_uri = "viking://user/other_user/memories/preferences/private.md"
    foreign_ctx = RequestContext(user=UserIdentifier(ctx.account_id, "other_user"), role=Role.USER)
    raw = 'Preference\n\n<!-- MEMORY_FIELDS\n{"memory_type": "preferences"}\n-->'
    await service.viking_fs.write_file(own_uri, raw, ctx=ctx)
    await service.viking_fs.write_file(foreign_uri, raw, ctx=foreign_ctx)
    monkeypatch.setattr(filesystem, "get_service", lambda: service)
    response = await filesystem.attrs_batch(
        request=filesystem.GetAttrsRequest(uris=[own_uri, foreign_uri]), _ctx=ctx
    )
    result = response.result
    assert [item["uri"] for item in result["items"]] == [own_uri]
    assert result["items"][0]["attrs"]["memory"]["memory_type"] == "preferences"
    assert "content" not in result["items"][0]["attrs"]["memory"]
    assert result["failed"][0]["uri"] == foreign_uri
    assert result["failed"][0]["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_attrs_batch_reports_vector_read_errors_as_failures(monkeypatch):
    fs = FSService(
        vikingdb=SimpleNamespace(filter=AsyncMock(side_effect=[RuntimeError("I/O failed"), []]))
    )
    monkeypatch.setattr(fs, "stat", AsyncMock(return_value={"isDir": False}))
    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=fs),
    )
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)
    first, second = "viking://resources/a.md", "viking://resources/b.md"
    response = await filesystem.attrs_batch(
        request=filesystem.GetAttrsRequest(uris=[first, second]), _ctx=ctx
    )
    result = response.result
    assert result["items"] == [{"uri": second, "context_type": "resource", "attrs": {"tags": []}}]
    assert result["failed"] == [
        {"uri": first, "error": {"code": "INTERNAL", "message": "Attribute read failed"}}
    ]


async def test_attrs_batch_keeps_same_uri_isolated_between_accounts(service, monkeypatch):
    from openviking.storage.vector_ids import vector_record_id
    from openviking.utils.time_utils import get_current_timestamp

    uri = "viking://resources/attrs-tenant/a.md"
    timestamp = get_current_timestamp()
    contexts = [
        RequestContext(user=service.user, role=Role.ROOT),
        RequestContext(user=UserIdentifier("attrs-other-account", "alice"), role=Role.ROOT),
    ]
    for count, ctx in enumerate(contexts, start=1):
        await service.viking_fs.write_file(uri, "content", ctx=ctx)
        await service.vikingdb_manager.upsert(
            {
                "id": vector_record_id(ctx.account_id, uri, 2),
                "uri": uri,
                "context_type": "resource",
                "level": 2,
                "abstract": "content",
                "created_at": timestamp,
                "updated_at": timestamp,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
                "search_tags": [f"used_count={count}"],
            },
            ctx=ctx,
        )
    monkeypatch.setattr(filesystem, "get_service", lambda: service)
    for count, ctx in enumerate(contexts, start=1):
        response = await filesystem.attrs_batch(
            request=filesystem.GetAttrsRequest(uris=[uri]), _ctx=ctx
        )
        result = response.result
        assert result["failed"] == []
        assert result["items"][0]["attrs"]["tags"] == [f"used_count={count}"]


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

    async def fake_stat(uri, ctx=None, include_lock_status=False, skip_count=False):
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

    viking_fs = SimpleNamespace(
        resolve_uri=AsyncMock(return_value=resolved_uri),
        stat=fake_stat,
    )
    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=FSService(viking_fs=viking_fs)),
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
    assert seen["uri"] == resolved_uri
    viking_fs.resolve_uri.assert_awaited_once_with(record_id, ctx=seen["ctx"])
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

    assert (
        await service.viking_fs.read_file_bytes(
            profile_uri,
            ctx=ctx,
        )
        == (builtin_memory_templates_dir() / "profile.yaml").read_bytes()
    )
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
