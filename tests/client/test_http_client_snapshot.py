"""Unit tests for AsyncHTTPClient git_* methods that drive /api/v1/snapshot/*."""

from typing import Any, Dict, List, Optional

import pytest

from openviking_cli.client.http import AsyncHTTPClient

pytestmark = pytest.mark.asyncio


class _FakeHTTPClient:
    """Records the last request and returns a canned response."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.next_response: Any = None

    async def get(self, path, *, params=None, headers=None):
        self.calls.append({"method": "GET", "path": path, "params": params, "headers": headers})
        return self.next_response

    async def post(self, path, *, json=None, headers=None):
        self.calls.append({"method": "POST", "path": path, "json": json, "headers": headers})
        return self.next_response


def _client_with_fake() -> tuple[AsyncHTTPClient, _FakeHTTPClient]:
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake = _FakeHTTPClient()
    client._http = fake
    client._handle_response = lambda response: {"commit_oid": "a" * 40, "result": "created", "changed": 1}
    return client, fake


async def test_git_commit_posts_to_snapshot_commit():
    client, fake = _client_with_fake()
    fake.next_response = object()

    result = await client.git_commit(
        message="hello",
        paths=["viking://resources/a.md"],
        branch="main",
        author_name="bot",
        author_email="bot@example.com",
    )

    assert result == {"commit_oid": "a" * 40, "result": "created", "changed": 1}
    call = fake.calls[-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/v1/snapshot/commit"
    assert call["json"] == {
        "message": "hello",
        "paths": ["viking://resources/a.md"],
        "branch": "main",
        "author_name": "bot",
        "author_email": "bot@example.com",
    }


async def test_git_commit_omits_none_fields():
    client, fake = _client_with_fake()
    fake.next_response = object()

    await client.git_commit(message="hi")

    call = fake.calls[-1]
    assert call["json"] == {"message": "hi", "branch": "main"}


async def test_git_restore_omits_project_dir_when_none():
    client, fake = _client_with_fake()
    client._handle_response = lambda resp: {"result": "noop", "head": "h" * 40, "source": "s" * 40}
    fake.next_response = object()

    result = await client.git_restore(
        source_commit="s" * 40,
        dry_run=True,
    )

    assert result["result"] == "noop"
    call = fake.calls[-1]
    assert call["method"] == "POST"
    assert call["path"] == "/api/v1/snapshot/restore"
    assert call["json"] == {
        "source_commit": "s" * 40,
        "branch": "main",
        "dry_run": True,
    }


async def test_git_restore_includes_project_dir_when_provided():
    client, fake = _client_with_fake()
    client._handle_response = lambda resp: {"result": "noop", "head": "h" * 40, "source": "s" * 40}
    fake.next_response = object()

    await client.git_restore(
        project_dir="viking://resources",
        source_commit="s" * 40,
        dry_run=True,
    )

    call = fake.calls[-1]
    assert call["json"] == {
        "source_commit": "s" * 40,
        "branch": "main",
        "dry_run": True,
        "project_dir": "viking://resources",
    }


class _FakeBinaryResponse:
    """Mimics enough of httpx.Response for the show-blob branch."""

    def __init__(self, content: bytes, oid: str, size: int):
        self.content = content
        self.status_code = 200
        self.headers = {
            "content-type": "application/octet-stream",
            "x-snapshot-oid": oid,
            "x-snapshot-size": str(size),
        }


async def test_git_show_metadata_uses_handle_response():
    client, fake = _client_with_fake()
    client._handle_response = lambda resp: {"oid": "c" * 40, "tree": "t" * 40, "message": "m"}
    fake.next_response = type("R", (), {"headers": {"content-type": "application/json"}})()

    result = await client.git_show(target_ref="c" * 40)

    assert result == {"oid": "c" * 40, "tree": "t" * 40, "message": "m"}
    call = fake.calls[-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/v1/snapshot/show"
    assert call["params"] == {"target_ref": "c" * 40}


async def test_git_show_blob_returns_envelope_from_headers():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake = _FakeHTTPClient()
    client._http = fake

    fake.next_response = _FakeBinaryResponse(b"hello\n", oid="b" * 40, size=6)

    result = await client.git_show(target_ref="c" * 40, path="viking://resources/x.txt")

    assert result == {"oid": "b" * 40, "size": 6, "bytes": b"hello\n"}
    call = fake.calls[-1]
    assert call["params"] == {"target_ref": "c" * 40, "path": "viking://resources/x.txt"}


async def test_git_show_blob_handles_case_insensitive_headers():
    """A real httpx.Response uses case-insensitive headers (httpx.Headers).
    Confirm the client reads x-snapshot-* correctly even when the server
    capitalises them (FastAPI may send X-Snapshot-Oid as-is).
    """
    import httpx

    client = AsyncHTTPClient(url="http://localhost:1933")
    fake = _FakeHTTPClient()
    client._http = fake

    class _CaseyResponse:
        def __init__(self, content: bytes, oid: str, size: int):
            self.content = content
            self.status_code = 200
            self.headers = httpx.Headers(
                {
                    "Content-Type": "application/octet-stream",
                    "X-Snapshot-Oid": oid,
                    "X-Snapshot-Size": str(size),
                }
            )

    fake.next_response = _CaseyResponse(b"\x00\x01\x02", oid="e" * 40, size=3)
    result = await client.git_show(target_ref="ref", path="viking://resources/bin.dat")
    assert result == {"oid": "e" * 40, "size": 3, "bytes": b"\x00\x01\x02"}


async def test_git_show_blob_missing_headers_degrades_gracefully():
    """Server bug / proxy strip: headers absent but content-type still binary.
    Client must not crash — empty oid and size=0 are acceptable fallbacks
    (the byte payload is what matters for the caller).
    """
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake = _FakeHTTPClient()
    client._http = fake

    class _NoHeadersResponse:
        def __init__(self, content: bytes):
            self.content = content
            self.status_code = 200
            self.headers = {"content-type": "application/octet-stream"}

    fake.next_response = _NoHeadersResponse(b"payload")
    result = await client.git_show(target_ref="r", path="viking://resources/x")
    assert result["bytes"] == b"payload"
    assert result["oid"] == ""
    assert result["size"] == 0


async def test_git_log_gets_with_params():
    client, fake = _client_with_fake()
    client._handle_response = lambda resp: [{"oid": "d" * 40, "message": "x"}]
    fake.next_response = object()

    result = await client.git_log(branch="main", limit=5)

    assert result == [{"oid": "d" * 40, "message": "x"}]
    call = fake.calls[-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/v1/snapshot/log"
    assert call["params"] == {"branch": "main", "limit": 5}
