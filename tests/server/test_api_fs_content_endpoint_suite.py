# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx

from openviking.pyagfs.exceptions import AGFSHTTPError
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import REINDEX_TASK_TYPE
from openviking.service.task_tracker import get_task_tracker
from openviking.storage.expr import Eq


def _assert_error(
    response: httpx.Response,
    *,
    status_code: int,
    error_code: str,
    message_fragment: str | None = None,
) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == error_code
    if message_fragment is not None:
        assert message_fragment in body["error"]["message"]


async def _request_with_handler(app, method: str, url: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, url, **kwargs)


async def test_ls_permission_denied_returns_structured_error(client, app, service):
    from openviking_cli.session.user_id import UserIdentifier

    root = "viking://user/alice/resources/private"
    uri = f"{root}/document.md"
    owner = RequestContext(
        user=UserIdentifier(service.user.account_id, "alice"), role=Role(Role.ROOT)
    )
    await service.viking_fs.write_file(uri, "private contents", ctx=owner)
    other = RequestContext(
        user=UserIdentifier(service.user.account_id, "bob"), role=Role(Role.USER)
    )
    app.dependency_overrides[get_request_context] = lambda: other
    attempts = (
        ("GET", "/api/v1/fs/ls", {"params": {"uri": root}}),
        ("POST", "/api/v1/fs/mkdir", {"json": {"uri": f"{root}/new"}}),
        ("POST", "/api/v1/content/write", {"json": {"uri": uri, "content": "overwrite"}}),
    )
    for method, endpoint, arguments in attempts:
        response = await client.request(method, endpoint, **arguments)
        _assert_error(response, status_code=403, error_code="PERMISSION_DENIED")
    assert not await service.viking_fs.exists(f"{root}/new", ctx=owner)
    assert await service.viking_fs.read_file(uri, ctx=owner) == "private contents"


async def test_tree_missing_uri_returns_not_found(client):
    missing = "viking://resources/missing"
    for endpoint in ("/api/v1/fs/tree", "/api/v1/content/read", "/api/v1/content/download"):
        response = await client.get(endpoint, params={"uri": missing})
        _assert_error(response, status_code=404, error_code="NOT_FOUND")

    to = "viking://resources/target"
    for operation in ("mv", "cp"):
        response = await client.post(
            f"/api/v1/fs/{operation}", json={"from_uri": missing, "to_uri": to}
        )
        _assert_error(response, status_code=404, error_code="NOT_FOUND")
    response = await client.get("/api/v1/fs/stat", params={"uri": to})
    _assert_error(response, status_code=404, error_code="NOT_FOUND")


async def test_stat_backend_unavailable_returns_structured_error(app, service, monkeypatch):
    async def fake_stat(*args, **kwargs):
        raise AGFSHTTPError("Internal server error", 500)

    monkeypatch.setattr(service.fs, "stat", fake_stat)
    response = await _request_with_handler(
        app,
        "GET",
        "/api/v1/fs/stat",
        params={"uri": "viking://resources/unavailable"},
    )
    _assert_error(response, status_code=503, error_code="UNAVAILABLE")


async def test_record_id_miss_hint_reaches_stat_and_read_responses(client):
    record_id = "0123456789abcdef0123456789abcdef"
    reason = "The data may not have been indexed yet or may have been deleted"

    for endpoint in ("/api/v1/fs/stat", "/api/v1/content/read"):
        response = await client.get(endpoint, params={"uri": record_id})
        _assert_error(
            response,
            status_code=404,
            error_code="NOT_FOUND",
            message_fragment=reason,
        )
        assert response.json()["error"]["details"]["reason"] == reason


async def test_cp_missing_target_parent_reports_directory(client, service):
    target_parent = "viking://resources/missing-parent"
    source = "viking://resources/source.md"
    ctx = RequestContext(user=service.user, role=Role(Role.ROOT))
    await service.viking_fs.write_file(source, "source bytes", ctx=ctx)

    response = await client.post(
        "/api/v1/fs/cp",
        json={"from_uri": source, "to_uri": f"{target_parent}/target.md"},
    )
    _assert_error(
        response,
        status_code=404,
        error_code="NOT_FOUND",
        message_fragment=f"Directory not found: {target_parent}",
    )
    assert response.json()["error"]["details"] == {"resource": target_parent, "type": "directory"}
    assert not await service.viking_fs.exists(target_parent, ctx=ctx)
    assert await service.viking_fs.read_file(source, ctx=ctx) == "source bytes"


async def test_cp_internal_rollback_error_is_redacted(app, service, monkeypatch):
    async def fake_cp(*args, **kwargs):
        raise RuntimeError("rollback failed with private vector payload")

    monkeypatch.setattr(service.fs, "cp", fake_cp, raising=False)
    response = await _request_with_handler(
        app,
        "POST",
        "/api/v1/fs/cp",
        json={
            "from_uri": "viking://resources/source",
            "to_uri": "viking://resources/target",
        },
    )

    _assert_error(response, status_code=500, error_code="INTERNAL")
    assert "private vector payload" not in response.text


async def test_download_returns_attachment_response(client, service):
    uri = "viking://resources/文档/记录 二进制.bin"
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    content = b"\x00\xffpayload"
    await service.viking_fs.write_file_bytes(uri, content, ctx=ctx)
    response = await client.get("/api/v1/content/download", params={"uri": uri})
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''%E8%AE%B0%E5%BD%95%20%E4%BA%8C%E8%BF%9B%E5%88%B6.bin"
    )


async def test_reindex_sync_conflict_returns_error_payload(client, app, service, monkeypatch):
    uri = "viking://resources/conflict.md"
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    await service.viking_fs.write_file(uri, "Existing file", ctx=ctx)
    monkeypatch.setitem(app.dependency_overrides, get_request_context, lambda: ctx)
    tracker = get_task_tracker()
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    active = await tracker.create_if_no_running(REINDEX_TASK_TYPE, uri, **owner)
    assert active is not None
    try:
        response = await client.post(
            "/api/v1/content/reindex",
            json={"uri": uri, "mode": "vectors_only", "wait": True},
        )
        _assert_error(response, status_code=409, error_code="CONFLICT")
        assert response.json()["error"]["details"] == {"uri": uri}
        stored = await tracker.get(active.task_id, **owner)
        assert stored is not None
        assert stored.status == active.status
    finally:
        await tracker.cancel(active.task_id, **owner)


async def test_consistency_reports_missing_index_until_sync_reindex(
    client, service, request_context: RequestContext
):
    root = "viking://resources/consistency"
    uri = f"{root}/note.md"
    ctx = request_context
    await service.viking_fs.write_file(uri, "A document with no vector yet.", ctx=ctx)

    file_response = await client.post("/api/v1/system/consistency", json={"uri": uri})
    _assert_error(file_response, status_code=400, error_code="INVALID_ARGUMENT")

    before = await client.post("/api/v1/system/consistency", json={"uri": root})
    assert before.status_code == 200, before.text
    assert before.json()["result"] == {
        "ok": False,
        "expected_count": 1,
        "missing_record_count": 1,
        "missing_records": [{"uri": uri, "path": "note.md", "level": 2, "key": "note.md#level=2"}],
        "missing_records_truncated": False,
    }

    rebuilt = await client.post(
        "/api/v1/content/reindex",
        json={"uri": uri, "mode": "vectors_only", "wait": True},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["result"]["status"] == "completed"
    after = await client.post("/api/v1/system/consistency", json={"uri": root})
    assert after.status_code == 200, after.text
    assert after.json()["result"]["ok"] is True
    assert after.json()["result"]["expected_count"] == 1
    assert after.json()["result"]["missing_records"] == []


async def test_reindex_async_task_produces_an_index_for_its_target(
    client, service, request_context: RequestContext
):
    uri = "viking://resources/background-reindex.md"
    ctx = request_context
    await service.viking_fs.write_file(uri, "Background reindex fixture.", ctx=ctx)

    accepted = await client.post(
        "/api/v1/content/reindex",
        json={"uri": uri, "mode": "vectors_only", "wait": False},
    )
    assert accepted.status_code == 200, accepted.text
    submission = accepted.json()["result"]
    assert submission["status"] == "accepted"
    assert submission["uri"] == uri
    task_id = submission["task_id"]
    await get_task_tracker().wait(
        task_id, account_id=ctx.account_id, user_id=ctx.user.user_id, timeout=15
    )

    status = await client.get(f"/api/v1/tasks/{task_id}")
    assert status.status_code == 200, status.text
    task = status.json()["result"]
    assert task["task_id"] == task_id
    assert task["status"] == "completed"
    assert task["resource_id"] == task["result"]["uri"] == uri
    records = await service.vikingdb_manager.filter(
        filter=Eq("uri", uri), output_fields=["uri", "level"], ctx=ctx
    )
    assert [(record["uri"], record["level"]) for record in records] == [(uri, 2)]
