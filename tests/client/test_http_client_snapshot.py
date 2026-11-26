"""Snapshot request contracts that are independent of the server's Git backend."""

import json

import httpx
import pytest
import pytest_asyncio

from openviking_cli.client.http import AsyncHTTPClient


@pytest_asyncio.fixture
async def snapshot_transport(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(tmp_path / "missing.conf"))
    requests = []

    def connect(*responses):
        remaining = iter(responses)

        def handle(request):
            requests.append(request)
            return next(remaining)

        return httpx.AsyncClient(
            base_url="http://localhost:1933", transport=httpx.MockTransport(handle)
        )

    client = AsyncHTTPClient(url="http://localhost:1933")
    try:
        yield client, connect, requests
    finally:
        await client.close()


async def test_git_commit_posts_to_snapshot_commit(snapshot_transport):
    client, connect, requests = snapshot_transport
    result = {"commit_oid": "a" * 40, "result": "created", "changed": 1}
    client._http = connect(
        httpx.Response(200, json={"status": "ok", "result": result}),
        httpx.Response(200, json={"status": "ok", "result": result}),
    )
    assert await client.git_commit(message="all resources") == result
    assert (
        await client.git_commit(
            message="one resource",
            paths=["viking://resources/a.md"],
            branch="main",
            author_name="bot",
            author_email="bot@example.com",
        )
        == result
    )

    assert [(r.method, r.url.path, json.loads(r.content)) for r in requests] == [
        ("POST", "/api/v1/snapshot/commit", {"message": "all resources", "branch": "main"}),
        (
            "POST",
            "/api/v1/snapshot/commit",
            {
                "message": "one resource",
                "paths": ["viking://resources/a.md"],
                "branch": "main",
                "author_name": "bot",
                "author_email": "bot@example.com",
            },
        ),
    ]


async def test_git_restore_omits_project_dir_when_none(snapshot_transport):
    client, connect, requests = snapshot_transport
    spec = {"source_commit": "a" * 40, "branch": "main", "dry_run": True}
    result = {"result": "noop"}
    client._http = connect(
        httpx.Response(200, json={"status": "ok", "result": result}),
        httpx.Response(200, json={"status": "ok", "result": result}),
    )
    assert await client.git_restore(source_commit=spec["source_commit"], dry_run=True) == result
    assert (
        await client.git_restore(
            project_dir="viking://resources", source_commit=spec["source_commit"], dry_run=True
        )
        == result
    )

    assert [(r.method, r.url.path, json.loads(r.content)) for r in requests] == [
        ("POST", "/api/v1/snapshot/restore", spec),
        ("POST", "/api/v1/snapshot/restore", {**spec, "project_dir": "viking://resources"}),
    ]


@pytest.mark.parametrize(
    ("snapshot_headers", "expected_oid", "expected_size"),
    [
        ({"X-Snapshot-Oid": "e" * 40, "X-Snapshot-Size": "3"}, "e" * 40, 3),
        ({}, "", 0),
    ],
    ids=["mixed-case-headers", "missing-snapshot-headers"],
)
async def test_git_show_blob_returns_envelope_from_headers(
    snapshot_transport, snapshot_headers, expected_oid, expected_size
):
    client, connect, requests = snapshot_transport
    uri = "viking://resources/bin.dat"
    content = b"\x00\x01\x02"
    client._http = connect(
        httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream", **snapshot_headers},
            content=content,
        )
    )
    assert await client.git_show(target_ref="ref", path=uri) == {
        "oid": expected_oid,
        "size": expected_size,
        "bytes": content,
    }

    (request,) = requests
    assert request.url.path == "/api/v1/snapshot/show"
    assert dict(request.url.params) == {"target_ref": "ref", "path": uri}


async def test_git_diff_gets_with_refs_and_path(snapshot_transport):
    client, connect, requests = snapshot_transport
    uri = "viking://resources/a.md"
    result = {"change_type": "modified", "diff_text": "@@ -1 +1 @@\n-old\n+new\n"}
    client._http = connect(
        httpx.Response(200, json={"status": "ok", "result": result}),
        httpx.Response(200, json={"status": "ok", "result": result}),
    )
    assert await client.git_diff(uri, from_ref="old", to_ref="new") == result
    assert await client.snapshot.diff(uri, to_ref="new") == result

    assert [(r.method, r.url.path, dict(r.url.params)) for r in requests] == [
        ("GET", "/api/v1/snapshot/diff", {"path": uri, "from": "old", "to": "new"}),
        ("GET", "/api/v1/snapshot/diff", {"path": uri, "to": "new"}),
    ]


async def test_snapshot_delete_posts_body(snapshot_transport):
    client, connect, requests = snapshot_transport
    ids = ["a" * 40]
    result = {"result": "dry_run", "deleted": ids}
    client._http = connect(httpx.Response(200, json={"status": "ok", "result": result}))
    actual = await client.snapshot.delete(ids, dry_run=True)
    assert actual == result

    (request,) = requests
    assert (request.method, request.url.path) == ("POST", "/api/v1/snapshot/delete")
    assert json.loads(request.content) == {"commit_ids": ids, "dry_run": True}
