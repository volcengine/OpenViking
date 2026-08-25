# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import io
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from openviking.pyagfs.exceptions import AGFSHTTPError
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
from openviking.server.routers import content as content_router


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


async def test_download_directory_returns_zip_archive(client_with_resource):
    client, uri = client_with_resource
    response = await client.get("/api/v1/content/download", params={"uri": uri})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert ".zip" in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert names
        root = names[0].rstrip("/")
        assert names[0].endswith("/")
        assert any(name.startswith(f"{root}/") and not name.endswith("/") for name in names)
        assert all(not name.startswith("/") and ".." not in name.split("/") for name in names)


async def test_download_directory_over_limit_returns_payload_too_large(
    client_with_resource, monkeypatch
):
    client, uri = client_with_resource
    monkeypatch.setattr(content_router, "_DIRECTORY_ARCHIVE_MAX_BYTES", 1)

    response = await client.get("/api/v1/content/download", params={"uri": uri})

    _assert_error(response, status_code=413, error_code="PAYLOAD_TOO_LARGE")


async def test_build_directory_archive_preserves_tree_and_empty_directories():
    class FakeFS:
        async def tree(self, *args, **kwargs):
            return [
                {"uri": "viking://resources/project/empty", "rel_path": "empty", "isDir": True},
                {
                    "uri": "viking://resources/project/docs/readme.md",
                    "rel_path": "docs/readme.md",
                    "isDir": False,
                    "size": 5,
                },
            ]

        async def read_file_bytes(self, uri, ctx):
            assert uri.endswith("docs/readme.md")
            return b"hello"

    service = SimpleNamespace(fs=FakeFS())
    payload, filename = await content_router._build_directory_archive(
        service,
        "viking://resources/project",
        {"name": "project", "isDir": True},
        SimpleNamespace(),
    )
    assert filename == "project.zip"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == [
            "project/",
            "project/empty/",
            "project/docs/readme.md",
        ]
        assert archive.read("project/docs/readme.md") == b"hello"


async def test_build_directory_archive_rejects_declared_size_over_limit():
    class FakeFS:
        async def tree(self, *args, **kwargs):
            return [
                {
                    "uri": "viking://resources/project/large.bin",
                    "rel_path": "large.bin",
                    "isDir": False,
                    "size": content_router._DIRECTORY_ARCHIVE_MAX_BYTES + 1,
                }
            ]

        async def read_file_bytes(self, *args, **kwargs):
            raise AssertionError("oversized file must not be read")

    service = SimpleNamespace(fs=FakeFS())
    with pytest.raises(Exception, match="exceeds the .* download limit"):
        await content_router._build_directory_archive(
            service,
            "viking://resources/project",
            {"name": "project", "isDir": True},
            SimpleNamespace(),
        )


async def test_build_directory_archive_rejects_too_many_entries_before_reading():
    observed = {}

    class FakeFS:
        async def tree(self, *args, **kwargs):
            observed["node_limit"] = kwargs.get("node_limit")
            return [
                {
                    "uri": f"viking://resources/project/d{index}",
                    "rel_path": f"d{index}",
                    "isDir": True,
                    "size": 0,
                }
                for index in range(content_router._DIRECTORY_ARCHIVE_MAX_ENTRIES + 1)
            ]

        async def read_file_bytes(self, *args, **kwargs):
            raise AssertionError("entry cap must fail before reading file contents")

    service = SimpleNamespace(fs=FakeFS())
    with pytest.raises(Exception, match="entry download limit"):
        await content_router._build_directory_archive(
            service,
            "viking://resources/project",
            {"name": "project", "isDir": True},
            SimpleNamespace(),
        )

    assert observed["node_limit"] == content_router._DIRECTORY_ARCHIVE_MAX_ENTRIES + 1


async def test_build_directory_archive_rejects_zip_over_limit(monkeypatch):
    class FakeFS:
        async def tree(self, *args, **kwargs):
            return [
                {
                    "uri": "viking://resources/project/empty",
                    "rel_path": "empty",
                    "isDir": True,
                    "size": 0,
                }
            ]

    monkeypatch.setattr(content_router, "_DIRECTORY_ARCHIVE_MAX_BYTES", 1)
    service = SimpleNamespace(fs=FakeFS())
    with pytest.raises(Exception, match="exceeds the 1-byte download limit"):
        await content_router._build_directory_archive(
            service,
            "viking://resources/project",
            {"name": "project", "isDir": True},
            SimpleNamespace(),
        )


async def test_build_directory_archive_stops_writing_once_over_limit(monkeypatch):
    """Header-only entries must not grow the archive buffer past the limit."""
    limit = 4096
    peak = {"bytes": 0}

    class FakeFS:
        async def tree(self, *args, **kwargs):
            return [
                {
                    "uri": f"viking://resources/project/d{index}",
                    "rel_path": f"d{index}",
                    "isDir": True,
                }
                for index in range(20000)
            ]

    class TrackedBytesIO(io.BytesIO):
        def write(self, data):
            count = super().write(data)
            peak["bytes"] = max(peak["bytes"], self.tell())
            return count

    monkeypatch.setattr(content_router, "_DIRECTORY_ARCHIVE_MAX_BYTES", limit)
    monkeypatch.setattr(content_router, "io", SimpleNamespace(BytesIO=TrackedBytesIO))

    with pytest.raises(Exception, match="download limit"):
        await content_router._build_directory_archive(
            SimpleNamespace(fs=FakeFS()),
            "viking://resources/project",
            {"name": "project", "isDir": True},
            SimpleNamespace(),
        )

    # Without the in-loop guard this reaches ~1.9 MB for 20k entries.
    assert peak["bytes"] < limit * 4


def test_archive_size_limit_error_is_not_retryable():
    """Oversize is a property of the directory, so it must be 413, not 429."""
    error = content_router._archive_size_limit_error("viking://resources/big")
    assert error.code == "PAYLOAD_TOO_LARGE"
    assert ERROR_CODE_TO_HTTP_STATUS[error.code] == 413


@pytest.mark.parametrize("path", ["../escape", "/absolute", r"..\\escape"])
def test_safe_archive_path_rejects_unsafe_members(path):
    with pytest.raises(Exception, match="Unsafe path"):
        content_router._safe_archive_path("project", path)


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
    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx, dry_run=False):
            from openviking_cli.exceptions import NotFoundError

            raise NotFoundError(uri, "resource")

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    response = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/missing", "mode": "vectors_only", "wait": True},
    )
    _assert_error(response, status_code=404, error_code="NOT_FOUND")


async def test_reindex_sync_conflict_returns_error_payload(client, monkeypatch):
    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx, dry_run=False):
            from openviking_cli.exceptions import OpenVikingError

            raise OpenVikingError(
                f"URI {uri} already has a reindex in progress",
                code="CONFLICT",
                details={"uri": uri},
            )

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    response = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/conflict", "mode": "vectors_only", "wait": True},
    )
    _assert_error(response, status_code=409, error_code="CONFLICT")


async def test_reindex_sync_success_returns_ok_payload(client, monkeypatch):
    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx, dry_run=False):
            return {"status": "completed", "mode": mode, "uri": uri}

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    response = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "mode": "semantic_and_vectors", "wait": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "completed"
    assert body["result"]["mode"] == "semantic_and_vectors"


async def test_reindex_async_returns_task_id(client, monkeypatch):
    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx, dry_run=False):
            return {
                "task_id": "task-123",
                "status": "accepted",
                "uri": uri,
                "object_type": "resource",
                "mode": mode,
            }

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    response = await client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "mode": "vectors_only", "wait": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["result"]["status"] == "accepted"
    assert body["result"]["task_id"] == "task-123"
    assert body["result"]["mode"] == "vectors_only"
