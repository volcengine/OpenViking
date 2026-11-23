"""Unit tests for AsyncHTTPClient git_* methods that drive /api/v1/snapshot/*."""

from typing import Any, Dict, List

import httpx
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


@pytest.mark.parametrize(
    ("snapshot_headers", "expected_oid", "expected_size"),
    [
        ({"X-Snapshot-Oid": "e" * 40, "X-Snapshot-Size": "3"}, "e" * 40, 3),
        ({}, "", 0),
    ],
    ids=["mixed-case-headers", "missing-snapshot-headers"],
)
async def test_git_show_blob_returns_envelope_from_headers(
    snapshot_headers, expected_oid, expected_size
):
    uri = "viking://resources/bin.dat"
    content = b"\x00\x01\x02"
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake = _FakeHTTPClient()
    client._http = fake
    fake.next_response = httpx.Response(
        200,
        headers={"Content-Type": "application/octet-stream", **snapshot_headers},
        content=content,
    )

    result = await client.git_show(target_ref="ref", path=uri)

    assert result == {"oid": expected_oid, "size": expected_size, "bytes": content}
    assert fake.calls[-1]["params"] == {"target_ref": "ref", "path": uri}


async def test_git_log_gets_with_params():
    """Verify explicit pagination and paths are sent to the log endpoint."""
    client, fake = _client_with_fake()
    client._handle_response = lambda resp: [{"oid": "d" * 40, "message": "x"}]
    fake.next_response = object()

    result = await client.git_log(
        branch="main",
        limit=5,
        offset=1,
        paths=["viking://resources/a.md", "viking://resources/docs"],
    )

    assert result == [{"oid": "d" * 40, "message": "x"}]
    call = fake.calls[-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/v1/snapshot/log"
    assert call["params"] == {
        "branch": "main",
        "limit": 5,
        "offset": 1,
        "paths": ["viking://resources/a.md", "viking://resources/docs"],
    }


async def test_git_log_empty_paths_is_unfiltered():
    """Verify empty paths stay unfiltered and an omitted offset defaults to zero."""
    client, fake = _client_with_fake()
    client._handle_response = lambda resp: [{"oid": "d" * 40, "message": "x"}]
    fake.next_response = object()

    await client.git_log(branch="main", limit=5, paths=[])

    call = fake.calls[-1]
    assert call["method"] == "GET"
    assert call["path"] == "/api/v1/snapshot/log"
    assert call["params"] == {"branch": "main", "limit": 5, "offset": 0}


async def test_git_diff_gets_with_refs_and_path():
    client, fake = _client_with_fake()
    client._handle_response = lambda resp: {
        "path": "viking://resources/a.md",
        "from_commit": "old",
        "to_commit": "new",
        "change_type": "modified",
        "diff_text": "@@ -1 +1 @@\n-old\n+new\n",
    }
    fake.next_response = object()

    result = await client.git_diff(
        "viking://resources/a.md",
        from_ref="old",
        to_ref="new",
    )

    assert result["change_type"] == "modified"
    call = fake.calls[-1]
    assert call == {
        "method": "GET",
        "path": "/api/v1/snapshot/diff",
        "params": {
            "path": "viking://resources/a.md",
            "from": "old",
            "to": "new",
        },
        "headers": None,
    }


async def test_git_diff_omits_optional_from_ref():
    client, fake = _client_with_fake()
    fake.next_response = object()

    await client.git_diff("viking://resources/a.md", to_ref="new")

    assert fake.calls[-1]["params"] == {
        "path": "viking://resources/a.md",
        "to": "new",
    }


async def test_snapshot_namespace_exposes_diff():
    client, fake = _client_with_fake()
    client._handle_response = lambda resp: {"change_type": "added", "diff_text": "+new\n"}
    fake.next_response = object()

    result = await client.snapshot.diff(
        "viking://resources/a.md",
        to_ref="new",
    )

    assert result["change_type"] == "added"
    assert fake.calls[-1]["params"] == {
        "path": "viking://resources/a.md",
        "to": "new",
    }
