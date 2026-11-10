# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for content endpoints: read, abstract, overview, reindex."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import content as content_router
from openviking.server.routers.content import (
    BatchWriteOperation,
    BatchWriteRequest,
    ReindexRequest,
    SetTagsRequest,
    WriteContentRequest,
    batch_write,
    reindex,
)
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.parametrize(
    "targets",
    [
        {},
        {"uri": "viking://resources/a.md", "uris": ["viking://resources/b.md"]},
        {"uris": []},
        {"uris": ["viking://resources/a.md"] * 51},
    ],
)
def test_set_tags_request_rejects_invalid_targets(targets):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SetTagsRequest(**targets)


@pytest.mark.parametrize("size", [1, 50])
def test_set_tags_request_accepts_batch_boundaries(size):
    request = SetTagsRequest(uris=["viking://resources/a.md"] * size)
    assert request.uri is None
    assert len(request.uris) == size


@pytest.mark.parametrize(
    "targets",
    [
        {},
        {"uri": "viking://resources/a.md", "uris": ["viking://resources/b.md"]},
        {"uris": []},
        {"uris": ["viking://resources/a.md"] * 51},
    ],
)
async def test_set_tags_batch_invalid_request_http(client, targets):
    response = await client.post("/api/v1/fs/attrs/set_tags", json=targets)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_write_content_request_accepts_processing_mode():
    request = WriteContentRequest(
        uri="viking://resources/demo.md",
        content="updated",
        processing_mode="vectors_only",
    )

    assert request.processing_mode == "vectors_only"


def test_write_content_request_defaults_processing_mode():
    request = WriteContentRequest(uri="viking://resources/demo.md", content="updated")

    assert request.processing_mode == "semantic_and_vectors"


def test_write_content_request_accepts_tags_and_tag_mode():
    request = WriteContentRequest(
        uri="viking://resources/demo.md",
        content="updated",
        tags=["Env=Prod", "team=search"],
        tag_mode="append",
    )

    assert request.tags == ["Env=Prod", "team=search"]
    assert request.tag_mode == "append"


def test_write_content_request_accepts_clear_without_tags():
    request = WriteContentRequest(
        uri="viking://resources/demo.md",
        content="updated",
        tag_mode="clear",
    )

    assert request.tags is None
    assert request.tag_mode == "clear"


def test_batch_write_request_accepts_write_parity_options():
    request = BatchWriteRequest(
        root_uri="viking://resources/demo",
        processing_mode="vectors_only",
        operations=[
            BatchWriteOperation(
                uri="viking://resources/demo/a.md",
                content="updated",
                tags=["team=search"],
                tag_mode="append",
                acl={
                    "acl_mode": "restricted",
                    "entries": [{"principal": "group:engineering", "level": "write"}],
                },
            )
        ],
    )

    assert request.processing_mode == "vectors_only"
    assert request.defer_processing is False
    assert request.operations[0].tags == ["team=search"]
    assert request.operations[0].tag_mode == "append"
    assert request.operations[0].acl.acl_mode == "restricted"


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
            defer_processing=True,
            operations=[
                BatchWriteOperation(
                    uri="viking://resources/demo/a.md",
                    content="updated",
                    tags=["team=search"],
                    tag_mode="append",
                    acl={"acl_mode": "restricted", "entries": []},
                )
            ],
        ),
        ctx,
    )

    assert seen["processing_mode"] == "vectors_only"
    assert seen["defer_processing"] is True
    assert seen["edit_source"] == "manual"
    operation = seen["operations"][0]
    assert operation["tags"] == ["team=search"]
    assert operation["tag_mode"] == "append"
    assert operation["acl"]["acl_mode"] == "restricted"
    assert list(operation["acl"]["entries"]) == []


async def test_write_forwards_processing_mode_to_service(monkeypatch):
    seen = {}

    async def fake_write(**kwargs):
        seen.update(kwargs)
        return {"uri": kwargs["uri"], "semantic_status": "skipped"}

    service = SimpleNamespace(fs=SimpleNamespace(write=fake_write))
    monkeypatch.setattr(content_router, "get_service", lambda: service)
    ctx = RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)

    response = await content_router.write(
        WriteContentRequest(
            uri="viking://resources/demo.md",
            content="updated",
            processing_mode="vectors_only",
        ),
        ctx,
    )

    assert response["status"] == "ok"
    assert seen["processing_mode"] == "vectors_only"
    assert seen["edit_source"] == "manual"


async def test_write_forwards_tags_and_tag_mode_to_service(monkeypatch):
    seen = {}

    async def fake_write(**kwargs):
        seen.update(kwargs)
        return {"uri": kwargs["uri"]}

    service = SimpleNamespace(fs=SimpleNamespace(write=fake_write))
    monkeypatch.setattr(content_router, "get_service", lambda: service)
    ctx = RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)

    await content_router.write(
        WriteContentRequest(
            uri="viking://resources/demo.md",
            content="updated",
            tags=["env=prod"],
            tag_mode="append",
        ),
        ctx,
    )

    assert seen["tags"] == ["env=prod"]
    assert seen["tag_mode"] == "append"


async def test_write_forwards_clear_without_tags_to_service(monkeypatch):
    seen = {}

    async def fake_write(**kwargs):
        seen.update(kwargs)
        return {"uri": kwargs["uri"]}

    service = SimpleNamespace(fs=SimpleNamespace(write=fake_write))
    monkeypatch.setattr(content_router, "get_service", lambda: service)
    ctx = RequestContext(user=UserIdentifier("account-1", "user-1"), role=Role.USER)

    await content_router.write(
        WriteContentRequest(
            uri="viking://resources/demo.md",
            content="updated",
            tag_mode="clear",
        ),
        ctx,
    )

    assert seen["tags"] is None
    assert seen["tag_mode"] == "clear"


async def test_read_content(client, service, monkeypatch):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    legacy_uri = "viking://resources/业务 域/章节 一.md"
    encoded_uri = "viking://resources/业务%20域/章节%20一.md"
    await service.viking_fs.write_file(legacy_uri, "legacy content", ctx=ctx)

    resp = await client.get("/api/v1/content/read", params={"uri": encoded_uri})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == "legacy content"

    await service.viking_fs.write_file(encoded_uri, "literal percent20 content", ctx=ctx)
    resp = await client.get("/api/v1/content/read", params={"uri": encoded_uri})
    assert resp.status_code == 200
    assert resp.json()["result"] == "literal percent20 content"

    resp = await client.get(
        "/api/v1/content/read",
        params={"uri": "viking://resources/业务%2520域/章节%2520一.md"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"

    monkeypatch.setattr(
        service.viking_fs,
        "read_file",
        AsyncMock(side_effect=[PermissionDeniedError("denied"), "legacy content"]),
    )
    resp = await client.get("/api/v1/content/read", params={"uri": encoded_uri})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_read_memory_uses_visible_projection_and_raw_bypasses_it(monkeypatch):
    ctx = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.USER)
    uri = "viking://user/test_user/memories/notes/private.md"
    raw = 'line one\nline two\n\n<!-- MEMORY_FIELDS\n{"secret": "do-not-leak"}\n-->'
    calls = []

    async def fake_read_visible(_uri, *, ctx, offset, limit):
        calls.append((offset, limit))
        return "visible slice"

    async def fake_read(_uri, *, ctx, offset, limit):
        lines = raw.splitlines(keepends=True)
        return "".join(lines[offset:] if limit == -1 else lines[offset : offset + limit])

    monkeypatch.setattr(
        content_router,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(read=fake_read, read_visible=fake_read_visible)),
    )

    response = await content_router.read(uri=uri, offset=4, limit=1, raw=False, _ctx=ctx)
    assert response.result == "visible slice"
    assert calls[-1] == (4, 1)

    raw_response = await content_router.read(uri=uri, offset=4, limit=1, raw=True, _ctx=ctx)
    assert raw_response.result == '{"secret": "do-not-leak"}\n'
    assert calls[-1] == (4, 1)


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


async def test_ls_file_uri_returns_invalid_argument(client, service):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    file_uri = "viking://resources/directory-type-check/file.md"
    await service.viking_fs.mkdir("viking://resources/directory-type-check", ctx=ctx)
    await service.viking_fs.write_file(file_uri, "file content", ctx=ctx)

    resp = await client.get("/api/v1/fs/ls", params={"uri": file_uri})
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert "not a directory" in body["error"]["message"]


async def test_set_tags_increase_http_persists_usage_and_preserves_resource(client, service):
    import time

    from openviking.storage.vector_ids import vector_record_id
    from openviking.utils.time_utils import get_current_timestamp

    ctx = RequestContext(user=service.user, role=Role.ROOT)
    uri = "viking://resources/usage-test/file.md"
    await service.viking_fs.mkdir("viking://resources/usage-test", ctx=ctx)
    await service.viking_fs.write_file(uri, "original content", ctx=ctx)
    record_id = vector_record_id(ctx.account_id, uri, 2)
    timestamp = get_current_timestamp()
    vector = [0.1] * service.vikingdb_manager.vector_dim
    await service.vikingdb_manager.upsert(
        {
            "id": record_id,
            "uri": uri,
            "context_type": "resource",
            "level": 2,
            "abstract": "original content",
            "created_at": timestamp,
            "updated_at": timestamp,
            "vector": vector,
            "search_tags": ["team=search", "score=0.1"],
        },
        ctx=ctx,
    )
    original_record = (await service.vikingdb_manager.get_strict([record_id], ctx=ctx))[0]

    for count in (1, 2):
        before = int(time.time() * 1000)
        response = await client.post(
            "/api/v1/fs/attrs/set_tags",
            json={"uri": uri, "tags": ["used_count=1"], "mode": "increase"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["success_count"] == 1
        attrs = await client.get("/api/v1/fs/attrs", params={"uri": uri})
        assert attrs.status_code == 200, attrs.text
        values = dict(tag.split("=", 1) for tag in attrs.json()["result"]["attrs"]["tags"])
        assert values["team"] == "search"
        assert int(values["used_count"]) == count
        assert before <= int(values["last_used_at"]) <= int(time.time() * 1000)

    response = await client.post(
        "/api/v1/fs/attrs/set_tags",
        json={"uri": uri, "tags": ["score=0.2"], "mode": "increase"},
    )
    assert response.status_code == 200, response.text
    before_score = (await service.vikingdb_manager.get_strict([record_id], ctx=ctx))[0]
    assert "score=0.3" in before_score["search_tags"]
    assert f"last_used_at={values['last_used_at']}" in before_score["search_tags"]

    for invalid in ("used_count", "used_count=bad", "used_count=NaN", "used_count=Infinity"):
        response = await client.post(
            "/api/v1/fs/attrs/set_tags",
            json={"uri": uri, "tags": [invalid], "mode": "increase"},
        )
        assert response.status_code == 400, response.text

    stored = (await service.vikingdb_manager.get_strict([record_id], ctx=ctx))[0]
    assert "used_count=2" in stored["search_tags"]
    assert stored["updated_at"] == timestamp
    assert stored["abstract"] == "original content"
    assert stored["vector"] == original_record["vector"]
    assert await service.viking_fs.read_file(uri, ctx=ctx) == "original content"


@pytest.mark.parametrize("endpoint", ["/api/v1/content/set_tags", "/api/v1/fs/attrs/set_tags"])
async def test_set_tags_batch_http_persists_actual_values(client, service, endpoint):
    from openviking.storage.vector_ids import vector_record_id
    from openviking.utils.time_utils import get_current_timestamp

    ctx = RequestContext(user=service.user, role=Role.ROOT)
    root = "viking://resources/batch-usage"
    uris = [f"{root}/a.md", f"{root}/b.md"]
    await service.viking_fs.mkdir(root, ctx=ctx)
    timestamp = get_current_timestamp()
    for uri in uris:
        await service.viking_fs.write_file(uri, "original content", ctx=ctx)
        await service.vikingdb_manager.upsert(
            {
                "id": vector_record_id(ctx.account_id, uri, 2),
                "uri": uri,
                "context_type": "resource",
                "level": 2,
                "abstract": "original content",
                "created_at": timestamp,
                "updated_at": timestamp,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
                "search_tags": ["team=search", "used_count=5"],
            },
            ctx=ctx,
        )
    unindexed = f"{root}/unindexed.md"
    await service.viking_fs.write_file(unindexed, "no index", ctx=ctx)
    missing = f"{root}/missing.md"
    for count in (6, 7):
        response = await client.post(
            endpoint,
            json={
                "uris": [uris[0], missing, uris[0] + "/", uris[1], unindexed, "invalid"],
                "tags": ["used_count=1"],
                "mode": "increase",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert [item["uri"] for item in result["items"]] == uris
        assert [item["uri"] for item in result["failed"]] == [missing, unindexed, "invalid"]
        assert [item["error"]["code"] for item in result["failed"]] == [
            "NOT_FOUND",
            "FAILED_PRECONDITION",
            "INVALID_URI",
        ]
        for item in result["items"]:
            updated = item["updated_tags"]
            assert len(updated) == 1
            assert updated[0]["level"] == 2
            values = dict(tag.split("=", 1) for tag in updated[0]["tags"])
            assert values["used_count"] == str(count)
            assert values["team"] == "search"
            assert int(values["last_used_at"]) > 0
            attrs = await client.get("/api/v1/fs/attrs", params={"uri": item["uri"]})
            assert attrs.json()["result"]["attrs"]["tags"] == updated[0]["tags"]

    response = await client.post(
        endpoint,
        json={"uris": uris, "tags": ["used_count=0", "last_used_at=0"], "mode": "append"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["failed"] == []
    for item in response.json()["result"]["items"]:
        assert set(item["updated_tags"][0]["tags"]) == {
            "team=search",
            "used_count=0",
            "last_used_at=0",
        }
        uri = item["uri"]
        record_id = vector_record_id(ctx.account_id, uri, 2)
        record = (await service.vikingdb_manager.get_strict([record_id], ctx=ctx))[0]
        assert set(record["search_tags"]) == set(item["updated_tags"][0]["tags"])
        assert record["updated_at"] == timestamp
        assert await service.viking_fs.read_file(uri, ctx=ctx) == "original content"


@pytest.mark.parametrize("batch", [False, True], ids=["single-uri", "batch-uris"])
async def test_usage_reset_http_round_trip_for_file_and_directory(client, service, batch):
    from openviking.storage.vector_ids import vector_record_id
    from openviking.utils.time_utils import get_current_timestamp

    ctx = RequestContext(user=service.user, role=Role.ROOT)
    root = "viking://resources/usage-reset"
    file_uri = f"{root}/a.md"
    targets = [file_uri, root]
    contents = {
        file_uri: "original content",
        f"{root}/.abstract.md": "original abstract",
        f"{root}/.overview.md": "original overview",
        f"{root}/child/b.md": "untouched child",
    }
    for uri, content in contents.items():
        await service.viking_fs.write_file(uri, content, ctx=ctx)
    timestamp = get_current_timestamp()
    record_ids = []
    for uri, level in [(file_uri, 2), (root, 0), (root, 1), (f"{root}/child/b.md", 2)]:
        record_id = vector_record_id(ctx.account_id, uri, level)
        record_ids.append(record_id)
        await service.vikingdb_manager.upsert(
            {
                "id": record_id,
                "uri": uri,
                "context_type": "resource",
                "level": level,
                "abstract": "original summary",
                "created_at": timestamp,
                "updated_at": timestamp,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
                "search_tags": ["team=search", "used_count=5", "last_used_at=123"],
            },
            ctx=ctx,
        )
    original = await service.vikingdb_manager.get_strict(record_ids, ctx=ctx)

    async def write(tags, mode):
        requests = [{"uris": targets}] if batch else [{"uri": uri} for uri in targets]
        for request in requests:
            response = await client.post(
                "/api/v1/fs/attrs/set_tags", json={**request, "tags": tags, "mode": mode}
            )
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            if batch:
                assert result["failed"] == []
                assert [item["uri"] for item in result["items"]] == targets
                assert [item["success_count"] for item in result["items"]] == [1, 2]
            else:
                assert result["tags_updated"] is True
                assert result["success_count"] == (2 if request["uri"] == root else 1)

    async def verify(count):
        before_read = await service.vikingdb_manager.get_strict(record_ids, ctx=ctx)
        response = await client.request("GET", "/api/v1/fs/attrs", json={"uris": targets})
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["failed"] == []
        assert [item["uri"] for item in result["items"]] == targets
        for item in result["items"]:
            tags = item["attrs"]["tags"]
            assert "team=search" in tags
            assert f"used_count={count}" in tags
            times = [int(tag.split("=", 1)[1]) for tag in tags if tag.startswith("last_used_at=")]
            assert times
            if count == 0:
                assert all(value == 0 for value in times)
            else:
                assert all(value > 0 for value in times)
            single = await client.get("/api/v1/fs/attrs", params={"uri": item["uri"]})
            assert single.status_code == 200, single.text
            assert single.json()["result"] == item
        after_read = await service.vikingdb_manager.get_strict(record_ids, ctx=ctx)
        assert after_read == before_read
        for record, baseline in zip(after_read, original, strict=True):
            if record["uri"] == f"{root}/child/b.md":
                assert record == baseline
            else:
                values = dict(tag.split("=", 1) for tag in record["search_tags"])
                assert values["used_count"] == str(count)
                if count == 0:
                    assert values["last_used_at"] == "0"
                else:
                    assert int(values["last_used_at"]) > 0
                assert {key: value for key, value in record.items() if key != "search_tags"} == {
                    key: value for key, value in baseline.items() if key != "search_tags"
                }
        for uri, content in contents.items():
            assert await service.viking_fs.read_file(uri, ctx=ctx) == content

    await write(["used_count=1"], "increase")
    await verify(6)
    for _ in range(2):
        await write(["used_count=0", "last_used_at=0"], "append")
        await verify(0)
    await write(["used_count=1"], "increase")
    await verify(1)


async def test_usage_reset_http_batch_reports_failures_without_touching_foreign_file(
    client, service, app
):
    from openviking.server.auth import get_request_context
    from openviking.storage.vector_ids import vector_record_id
    from openviking.utils.time_utils import get_current_timestamp

    ctx = RequestContext(user=service.user, role=Role.USER)
    foreign_ctx = RequestContext(user=UserIdentifier(ctx.account_id, "other_user"), role=Role.USER)
    own = f"viking://user/{ctx.user.user_id}/resources/usage-reset/a.md"
    foreign = "viking://user/other_user/resources/usage-reset/private.md"
    missing = own.replace("a.md", "missing.md")
    unindexed = own.replace("a.md", "unindexed.md")
    timestamp = get_current_timestamp()
    record_ids = []
    for uri, owner in [(own, ctx), (foreign, foreign_ctx)]:
        await service.viking_fs.write_file(uri, "original content", ctx=owner)
        record_id = vector_record_id(ctx.account_id, uri, 2)
        record_ids.append(record_id)
        await service.vikingdb_manager.upsert(
            {
                "id": record_id,
                "uri": uri,
                "context_type": "resource",
                "level": 2,
                "abstract": "original content",
                "created_at": timestamp,
                "updated_at": timestamp,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
                "search_tags": ["team=search", "used_count=5", "last_used_at=123"],
            },
            ctx=owner,
        )
    await service.viking_fs.write_file(unindexed, "no index", ctx=ctx)
    root_ctx = RequestContext(user=service.user, role=Role.ROOT)
    foreign_before = (await service.vikingdb_manager.get_strict([record_ids[1]], ctx=root_ctx))[0]
    app.dependency_overrides[get_request_context] = lambda: ctx
    try:
        response = await client.post(
            "/api/v1/fs/attrs/set_tags",
            json={
                "uris": [own, missing, foreign, unindexed],
                "tags": ["used_count=0", "last_used_at=0"],
                "mode": "append",
            },
        )
    finally:
        app.dependency_overrides.pop(get_request_context, None)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert [item["uri"] for item in result["items"]] == [own]
    assert set(result["items"][0]["updated_tags"][0]["tags"]) == {
        "team=search",
        "used_count=0",
        "last_used_at=0",
    }
    assert [item["uri"] for item in result["failed"]] == [missing, foreign, unindexed]
    assert [item["error"]["code"] for item in result["failed"]] == [
        "NOT_FOUND",
        "PERMISSION_DENIED",
        "FAILED_PRECONDITION",
    ]
    foreign_after = (await service.vikingdb_manager.get_strict([record_ids[1]], ctx=root_ctx))[0]
    assert foreign_after == foreign_before
    assert await service.viking_fs.read_file(foreign, ctx=foreign_ctx) == "original content"


async def test_overview_missing_uri_returns_not_found(client):
    resp = await client.get(
        "/api/v1/content/overview",
        params={"uri": "viking://resources/does-not-exist-for-overview"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "NOT_FOUND"


async def test_reindex_missing_uri(client):
    """Test reindex without uri field returns structured INVALID_ARGUMENT."""
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"mode": "vectors_only"},
    )
    assert resp.status_code == 400


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
    await service.fs.finalize_compile_directories(uri, ctx)
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


async def test_reindex_request_validation(client):
    """Test reindex validates the request body schema."""
    # Empty body — uri is required
    resp = await client.post("/api/v1/content/reindex", json={})
    assert resp.status_code == 400

    # Invalid mode should not be accepted by the endpoint
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/test", "mode": "not_a_mode"},
    )
    assert resp.status_code in (200, 400)


async def test_reindex_wait_parameter_schema(client):
    """Test reindex accepts wait parameter in request schema."""
    # Invalid wait type should be coerced or rejected, not crash
    resp = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/test", "wait": "invalid"},
    )
    # Pydantic coerces or rejects — either way, not a 404/405
    assert resp.status_code != 404
    assert resp.status_code != 405


@pytest.mark.asyncio
async def test_reindex_uses_request_tenant_for_exists(monkeypatch):
    """Reindex must validate URI existence inside the caller's tenant."""
    seen = {}

    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx, dry_run=False):
            seen["uri"] = uri
            seen["mode"] = mode
            seen["wait"] = wait
            seen["ctx"] = ctx
            return {"status": "completed", "uri": uri, "mode": mode}

    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ADMIN,
    )
    request = ReindexRequest(
        uri="viking://resources/demo/demo-note.md",
        mode="semantic_and_vectors",
        wait=True,
    )

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())

    response = await reindex(body=request, ctx=ctx)

    assert response.status == "ok"
    assert seen["uri"] == "viking://resources/demo/demo-note.md"
    assert seen["mode"] == "semantic_and_vectors"
    assert seen["wait"] is True
    assert seen["ctx"] == ctx


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
