# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import httpx

from openviking.pyagfs.exceptions import AGFSHTTPError


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


async def _first_child_uri(client: httpx.AsyncClient, uri: str) -> str:
    response = await client.get(
        "/api/v1/fs/ls",
        params={"uri": uri, "simple": True, "recursive": True, "output": "original"},
    )
    children = response.json().get("result", [])
    if children and isinstance(children[0], str):
        return children[0]
    return uri


async def _request_with_handler(app, method: str, url: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, url, **kwargs)


class _FakeVikingFS:
    def __init__(self, exists_result: bool):
        self._exists_result = exists_result

    async def exists(self, uri, ctx=None):
        return self._exists_result


class _FakeTracker:
    def __init__(self, *, has_running: bool = False, task_id: str | None = None):
        self._has_running = has_running
        self._task_id = task_id

    def has_running(self, *args, **kwargs):
        return self._has_running

    def create_if_no_running(self, *args, **kwargs):
        if self._task_id is None:
            return None
        return SimpleNamespace(task_id=self._task_id)


async def test_ls_permission_denied_returns_structured_error(app, service, monkeypatch):
    async def fake_ls(*args, **kwargs):
        raise PermissionError("Access denied for viking://resources")

    monkeypatch.setattr(service.fs, "ls", fake_ls)
    response = await _request_with_handler(
        app,
        "GET",
        "/api/v1/fs/ls",
        params={"uri": "viking://resources"},
    )
    _assert_error(
        response,
        status_code=403,
        error_code="PERMISSION_DENIED",
        message_fragment="Access denied",
    )


async def test_tree_missing_uri_returns_not_found(app, service, monkeypatch):
    async def fake_tree(*args, **kwargs):
        raise FileNotFoundError("tree target missing")

    monkeypatch.setattr(service.fs, "tree", fake_tree)
    response = await _request_with_handler(
        app,
        "GET",
        "/api/v1/fs/tree",
        params={"uri": "viking://resources/missing"},
    )
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


async def test_mkdir_permission_denied_returns_structured_error(app, service, monkeypatch):
    async def fake_mkdir(*args, **kwargs):
        raise PermissionError("Access denied for viking://resources/blocked")

    monkeypatch.setattr(service.fs, "mkdir", fake_mkdir)
    response = await _request_with_handler(
        app,
        "POST",
        "/api/v1/fs/mkdir",
        json={"uri": "viking://resources/blocked"},
    )
    _assert_error(response, status_code=403, error_code="PERMISSION_DENIED")


async def test_mv_missing_source_returns_not_found(app, service, monkeypatch):
    async def fake_mv(*args, **kwargs):
        raise FileNotFoundError("mv source not found")

    monkeypatch.setattr(service.fs, "mv", fake_mv)
    response = await _request_with_handler(
        app,
        "POST",
        "/api/v1/fs/mv",
        json={
            "from_uri": "viking://resources/missing",
            "to_uri": "viking://resources/target",
        },
    )
    _assert_error(response, status_code=404, error_code="NOT_FOUND")


async def test_read_missing_uri_returns_not_found(app, service, monkeypatch):
    async def fake_read(*args, **kwargs):
        raise FileNotFoundError("read target missing")

    monkeypatch.setattr(service.fs, "read", fake_read)
    response = await _request_with_handler(
        app,
        "GET",
        "/api/v1/content/read",
        params={"uri": "viking://resources/missing.md"},
    )
    _assert_error(response, status_code=404, error_code="NOT_FOUND")


async def test_download_returns_attachment_response(client_with_resource):
    client, uri = client_with_resource
    file_uri = await _first_child_uri(client, uri)
    response = await client.get("/api/v1/content/download", params={"uri": file_uri})
    assert response.status_code == 200
    assert response.content
    assert response.headers["content-type"] == "application/octet-stream"
    assert "attachment;" in response.headers["content-disposition"]


async def test_download_missing_uri_returns_not_found(app, service, monkeypatch):
    async def fake_read_file_bytes(*args, **kwargs):
        raise FileNotFoundError("download target missing")

    monkeypatch.setattr(service.fs, "read_file_bytes", fake_read_file_bytes)
    response = await _request_with_handler(
        app,
        "GET",
        "/api/v1/content/download",
        params={"uri": "viking://resources/missing.bin"},
    )
    _assert_error(response, status_code=404, error_code="NOT_FOUND")


async def test_write_permission_denied_returns_structured_error(app, service, monkeypatch):
    async def fake_write(*args, **kwargs):
        raise PermissionError("Access denied for viking://resources/protected.md")

    monkeypatch.setattr(service.fs, "write", fake_write)
    response = await _request_with_handler(
        app,
        "POST",
        "/api/v1/content/write",
        json={
            "uri": "viking://resources/protected.md",
            "content": "hello",
            "mode": "replace",
        },
    )
    _assert_error(response, status_code=403, error_code="PERMISSION_DENIED")


async def test_reindex_missing_uri_returns_not_found_error_payload(client, monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: _FakeVikingFS(False),
    )
    response = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/missing", "wait": True},
    )
    _assert_error(response, status_code=200, error_code="NOT_FOUND")


async def test_reindex_sync_conflict_returns_error_payload(client, monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: _FakeVikingFS(True),
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: _FakeTracker(has_running=True),
    )
    monkeypatch.setattr(
        "openviking.server.routers.content.get_service",
        lambda: SimpleNamespace(),
    )
    response = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/conflict", "wait": True},
    )
    _assert_error(response, status_code=200, error_code="CONFLICT")


async def test_reindex_sync_success_returns_ok_payload(client, monkeypatch):
    async def fake_do_reindex(service, uri, regenerate, ctx):
        return {"status": "success", "message": "Indexed 1 resources"}

    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: _FakeVikingFS(True),
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: _FakeTracker(has_running=False),
    )
    monkeypatch.setattr(
        "openviking.server.routers.content.get_service",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr("openviking.server.routers.content._do_reindex", fake_do_reindex)
    response = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "wait": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "success"


async def test_reindex_async_returns_task_id(client, monkeypatch):
    created = {"called": False}

    def fake_create_task(coro):
        created["called"] = True
        coro.close()
        return None

    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: _FakeVikingFS(True),
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: _FakeTracker(task_id="task-123"),
    )
    monkeypatch.setattr(
        "openviking.server.routers.content.get_service",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr("openviking.server.routers.content.asyncio.create_task", fake_create_task)
    response = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "wait": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "accepted"
    assert body["result"]["task_id"] == "task-123"
    assert created["called"] is True
