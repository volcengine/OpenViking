# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for content endpoints: read, abstract, overview, reindex."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.openapi.utils import get_openapi

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import content as content_router
from openviking.server.routers.content import (
    BatchWriteOperation,
    BatchWriteRequest,
    batch_write,
)
from openviking.service.task_tracker import get_task_tracker
from openviking_cli.exceptions import ConflictError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_api_resources import _wait_task_terminal


async def test_batch_write_forwards_processing_mode_and_operation_metadata(monkeypatch):
    seen = {}

    async def fake_batch_write(**kwargs):
        seen.update(kwargs)
        return {"updated": [kwargs["operations"][0]["uri"]]}

    monkeypatch.setattr(
        content_router,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(batch_write=fake_batch_write)),
    )
    ctx = RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)

    await batch_write(
        BatchWriteRequest(
            root_uri="viking://resources/demo",
            processing_mode="vectors_only",
            use_snapshot=True,
            operations=[
                BatchWriteOperation(
                    uri="viking://resources/demo/a.md",
                    content="updated",
                    resolve_conflicts=True,
                    tags=["team=search"],
                    tag_mode="append",
                    acl={"acl_mode": "restricted", "entries": []},
                )
            ],
        ),
        ctx,
    )

    assert seen["processing_mode"] == "vectors_only"
    assert seen["defer_processing"] is False
    assert seen["use_snapshot"] is True
    operation = seen["operations"][0]
    assert operation["resolve_conflicts"] is True
    assert operation["tags"] == ["team=search"]
    assert operation["tag_mode"] == "append"
    assert operation["acl"]["acl_mode"] == "restricted"
    assert list(operation["acl"]["entries"]) == []


async def test_write_processing_mode_and_deferred_snapshot(client, service, request_context):
    uri = "viking://resources/write-deferred/page.md"
    deferred = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "Saved draft",
            "defer_processing": True,
            "use_snapshot": True,
            "wait": True,
        },
    )
    assert deferred.status_code == 200, deferred.text
    result = deferred.json()["result"]
    assert result["semantic_status"] == result["vector_status"] == "deferred"
    versions = await service.fs.file_versions(uri, request_context, limit=10)
    assert len(versions["versions"]) == 1
    oid = versions["versions"][0]["oid"]
    assert await service.viking_fs.show(oid, path=uri, ctx=request_context) == b"Saved draft"

    vector_write = await client.post(
        "/api/v1/content/write",
        json={
            "uri": uri,
            "content": "Indexed draft",
            "processing_mode": "vectors_only",
            "wait": True,
        },
    )
    assert vector_write.status_code == 200, vector_write.text
    result = vector_write.json()["result"]
    assert result["semantic_status"] == "skipped"
    assert result["vector_status"] == "complete"


async def test_read_content(client, service, monkeypatch):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    legacy_uri = "viking://resources/业务 域/章节 一.md"
    encoded_uri = "viking://resources/业务%20域/章节%20一.md"
    text = "ascii\n" * 11000 + '中文🙂"\\\r\n\t\x00\u2028\u2029'
    for uri, content in ((legacy_uri, ""), (encoded_uri, text)):
        await service.viking_fs.write_file(uri, content, ctx=ctx)
        resp = await client.get("/api/v1/content/read", params={"uri": encoded_uri})
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "ok",
            "result": content,
            "error": None,
            "telemetry": None,
            "profile": None,
        }
        assert resp.headers["content-type"] == "application/json"
        assert int(resp.headers["content-length"]) == len(resp.content)

    schema = get_openapi(title="Content API", version="1", routes=content_router.router.routes)
    success = schema["paths"]["/api/v1/content/read"]["get"]["responses"]["200"]
    assert success["content"]["application/json"]["schema"]["$ref"].endswith("/Response")

    resp = await client.get("/api/v1/content/read")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ARGUMENT"

    resp = await client.get(
        "/api/v1/content/read",
        params={"uri": "viking://resources/业务%2520域/章节%2520一.md"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"

    reader = AsyncMock(side_effect=[PermissionDeniedError("denied"), "legacy content"])
    monkeypatch.setattr(service.viking_fs, "read_file", reader)
    resp = await client.get("/api/v1/content/read", params={"uri": encoded_uri})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PERMISSION_DENIED"
    reader.assert_awaited_once()


async def test_read_memory_uses_visible_projection_and_raw_bypasses_it(
    client, service, monkeypatch
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    uri = "viking://user/test_user/memories/notes/private.md"
    metadata = '{"example": "stored metadata"}'
    raw_content = f"line one\nline two\n\n<!-- MEMORY_FIELDS\n{metadata}\n-->"
    await service.viking_fs.write_file(uri, raw_content, ctx=ctx)
    live_read = AsyncMock(return_value="live context")
    monkeypatch.setattr(service.mcps, "read_live_context", live_read)

    visible = await client.get("/api/v1/content/read", params={"uri": uri, "offset": 1, "limit": 1})
    assert visible.status_code == 200
    assert visible.json()["result"] == "line two"

    raw = await client.get(
        "/api/v1/content/read", params={"uri": uri, "offset": 4, "limit": 1, "raw": True}
    )
    assert raw.status_code == 200
    assert raw.json()["result"] == metadata + "\n"

    full = await client.get("/api/v1/content/read", params={"uri": uri, "raw": True})
    assert full.status_code == 200
    assert full.json()["result"] == raw_content
    live_read.assert_not_awaited()

    live = await client.get("/api/v1/content/read", params={"uri": uri})
    assert live.status_code == 200
    assert live.json()["result"] == "live context"
    live_read.assert_awaited_once()


async def test_read_directory_uri_returns_invalid_argument(client_with_resource):
    client, uri = client_with_resource

    resp = await client.get("/api/v1/content/read", params={"uri": uri})

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "Directory URI is not readable as a file" in body["error"]["message"]
    assert "List it first, then read a file URI." in body["error"]["message"]
    assert body["error"]["details"] == {
        "resource": uri,
        "expected": "file",
        "actual": "directory",
    }


@pytest.mark.parametrize("uri", ["viking://temp/generated", "viking://queue/tasks"])
async def test_read_internal_scope_uri_returns_invalid_uri(client, uri: str):
    resp = await client.get("/api/v1/content/read", params={"uri": uri})

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_URI"
    assert "Must be one of" in body["error"]["message"]
    assert "frozenset" not in body["error"]["message"]


async def test_abstract_content(client_with_resource, service):
    client, uri = client_with_resource
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await service.viking_fs.write_file(f"{uri}/.abstract.md", "---\n", ctx=ctx)

    resp = await client.get("/api/v1/content/abstract", params={"uri": uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == f"# {uri} [Directory abstract is not ready]"


async def test_overview_content(client_with_resource, service):
    client, uri = client_with_resource
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await service.viking_fs.write_file(f"{uri}/.overview.md", "---\n", ctx=ctx)

    resp = await client.get("/api/v1/content/overview", params={"uri": uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == f"# {uri}\n\n[Directory overview is not ready]"


async def test_listing_a_file_returns_one_entry(client, service):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    file_uri = "viking://resources/directory-type-check/file.md"
    await service.viking_fs.mkdir("viking://resources/directory-type-check", ctx=ctx)
    await service.viking_fs.write_file(file_uri, "file content", ctx=ctx)

    for endpoint, output in (("ls", "original"), ("tree", "agent")):
        response = await client.get(
            f"/api/v1/fs/{endpoint}", params={"uri": file_uri, "output": output}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        (entry,) = body["result"]
        assert entry["uri"] == file_uri
        assert entry["isDir"] is False
        assert entry["name" if endpoint == "ls" else "rel_path"] == "file.md"
        assert body["has_more"] is False

    next_page = await client.get("/api/v1/fs/tree", params={"uri": file_uri, "offset": 1})
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["result"] == []


async def test_overview_missing_uri_returns_not_found(client):
    resp = await client.get(
        "/api/v1/content/overview",
        params={"uri": "viking://resources/does-not-exist-for-overview"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "NOT_FOUND"


async def test_directory_index_preserves_summaries_and_tags(client, service):
    """Directory indexing preserves content; Compile tags belong only to the target root."""
    from openviking.storage.expr import PathScope

    ctx = RequestContext(user=service.user, role=Role.ROOT)
    uri = "viking://resources/compile"
    await service.viking_fs.mkdir(f"{uri}/child", ctx=ctx)
    seeded = await client.post(
        "/api/v1/content/ensure_directory_index",
        json={"uri": uri, "fallback_abstract": "Compile output"},
    )
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["result"]["created_abstract"] is True
    assert seeded.json()["result"]["indexed_levels"] == [0]
    await service.fs.write(f"{uri}/child/note.md", "Content", ctx=ctx, wait=True)
    await service.resources.wait_processed()
    original = await service.viking_fs.read_file(f"{uri}/.abstract.md", ctx=ctx)
    await service.fs.set_tags(uri, ["team=search"], mode="append", recursive=False, ctx=ctx)
    tagging = await service.fs.ensure_compile_target(uri, ctx)
    await get_task_tracker().wait(
        tagging["task_id"], account_id=ctx.account_id, user_id=ctx.user.user_id, timeout=30
    )
    repeated = await client.post(
        "/api/v1/content/ensure_directory_index",
        json={"uri": uri, "fallback_abstract": "Do not replace stored summaries"},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["result"]["created_abstract"] is False
    assert await service.viking_fs.read_file(f"{uri}/.abstract.md", ctx=ctx) == original
    assert await service.fs.read(f"{uri}/child/note.md", ctx) == "Content"
    records = await service.vikingdb_manager.filter(
        filter=PathScope("uri", uri), output_fields=["uri", "level", "search_tags"], ctx=ctx
    )
    root_records = [record for record in records if record["uri"] == uri and record["level"] < 2]
    assert any(record["level"] == 0 for record in root_records)
    assert all(
        {"compile_target=true", "team=search"} <= set(record["search_tags"])
        for record in root_records
    )
    assert all(
        "compile_target=true" not in (record.get("search_tags") or [])
        for record in records
        if record not in root_records
    )


async def test_tagged_output_snapshots_writes_and_blocks_resource_replacement(client, service):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    parent = "viking://resources/tagged-output-parent"
    output = f"{parent}/output"
    tagging = await service.fs.ensure_compile_target(output, ctx)
    await get_task_tracker().wait(
        tagging["task_id"], account_id=ctx.account_id, user_id=ctx.user.user_id, timeout=30
    )
    uri = f"{output}/child/note.md"

    await service.fs.write(uri, "tagged output", ctx, wait=True, use_snapshot=True)

    history = await service.fs.file_versions(uri, ctx, limit=10)
    from openviking.storage.expr import Eq

    tagged = await service.vikingdb_manager.filter(
        filter=Eq("search_tags", "compile_target=true"),
        output_fields=["uri", "level", "search_tags"],
        ctx=ctx,
    )
    assert all(record["uri"].startswith(output) for record in tagged), tagged
    assert len(history["versions"]) == 1
    assert await service.viking_fs.show(history["versions"][0]["oid"], path=uri, ctx=ctx) == (
        b"tagged output"
    )
    for destination in (output, uri, parent):
        with pytest.raises(ConflictError, match="tagged Compile output"):
            await service.resources._ensure_resource_target_available(destination, ctx)
    await service.resources._ensure_resource_target_available(parent, ctx, is_parent=True)
    await service.resources._ensure_resource_target_available(f"{output}-sibling", ctx)


async def test_reindex_request_validation(client_with_resource):
    client, root = client_with_resource
    uri = f"{root}/sample.md"
    for payload, field in (({}, "uri"), ({"uri": uri, "wait": "invalid"}, "wait")):
        response = await client.post("/api/v1/content/reindex", json=payload)
        assert response.status_code == 400, response.text
        error = response.json()["error"]
        assert error["code"] == "INVALID_ARGUMENT"
        [validation] = error["details"]["validation_errors"]
        assert validation["loc"] == ["body", field]

    unsupported = await client.post(
        "/api/v1/content/reindex", json={"uri": uri, "mode": "not_a_mode"}
    )
    assert unsupported.status_code == 400, unsupported.text
    error = unsupported.json()["error"]
    assert error["code"] == "UNSUPPORTED_MODE"
    assert error["details"]["mode"] == "not_a_mode"
    assert "vectors_only" in error["details"]["supported_modes"]


async def test_reindex_uses_request_tenant_for_exists(client_with_resource, app, monkeypatch):
    client, root = client_with_resource
    uri = f"{root}/sample.md"
    foreign = RequestContext(user=UserIdentifier("other-account", "default"), role=Role.ADMIN)
    monkeypatch.setitem(app.dependency_overrides, get_request_context, lambda: foreign)
    payload = {"uri": uri, "mode": "vectors_only", "wait": False}

    unavailable = await client.post("/api/v1/content/reindex", json=payload)
    assert unavailable.status_code == 404, unavailable.text
    assert unavailable.json()["error"]["code"] == "NOT_FOUND"

    owner = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    monkeypatch.setitem(app.dependency_overrides, get_request_context, lambda: owner)
    accepted = await client.post("/api/v1/content/reindex", json=payload)
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()["result"]
    assert result["uri"] == uri
    assert result["status"] == "accepted"
    task = await _wait_task_terminal(client, result["task_id"])
    assert task["status"] == "completed", task
    assert task["resource_id"] == uri


async def test_content_rebuild_endpoint_removed(client):
    response = await client.post(
        "/api/v1/content/rebuild",
        json={"uri": "viking://resources/demo", "mode": "vectors_only"},
    )
    assert response.status_code == 404


async def test_maintenance_reindex_endpoint_removed(client):
    response = await client.post(
        "/api/v1/maintenance/reindex",
        json={"uri": "viking://resources/demo", "wait": True},
    )
    assert response.status_code == 404
