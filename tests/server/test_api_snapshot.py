# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end tests for /api/v1/snapshot/*."""

import pytest
import pytest_asyncio

import httpx

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="function")
async def client_with_no_repo(app):
    """Plain in-process client with no resources or commits added.

    The conftest's ``app`` fixture wires the service into the global
    dependency store without authentication, so a vanilla AsyncClient
    is enough to hit ``/api/v1/snapshot/log`` against an empty repo.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_commit_creates_snapshot(client_with_resource):
    client, _root_uri = client_with_resource
    resp = await client.post(
        "/api/v1/snapshot/commit",
        json={"message": "first snapshot"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["result"] in ("created", "noop")
    assert isinstance(result["commit_oid"], str) and len(result["commit_oid"]) == 40


async def test_log_returns_recent_commits(client_with_resource):
    client, _ = client_with_resource
    await client.post("/api/v1/snapshot/commit", json={"message": "for log"})

    resp = await client.get("/api/v1/snapshot/log", params={"branch": "main", "limit": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    log = body["result"]
    assert isinstance(log, list) and len(log) >= 1
    assert "oid" in log[0] and "message" in log[0]


async def test_log_empty_repo_returns_404(client_with_no_repo):
    """When the branch has no commits, /log should surface 404."""
    client = client_with_no_repo
    resp = await client.get("/api/v1/snapshot/log", params={"branch": "main", "limit": 5})
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


@pytest_asyncio.fixture(scope="function")
async def client_with_resource_and_blob(client_with_resource, service):
    """client_with_resource + a known blob written via VikingFS.write_file, then committed."""
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    client, _root = client_with_resource
    blob_uri = "viking://resources/snapshot_blob_fixture.txt"
    expected_bytes = b"hello from snapshot fixture\n"

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await service.viking_fs.write_file(blob_uri, expected_bytes, ctx=ctx)

    commit_resp = await client.post(
        "/api/v1/snapshot/commit",
        json={"message": "with blob"},
    )
    assert commit_resp.status_code == 200, commit_resp.text
    commit_oid = commit_resp.json()["result"]["commit_oid"]

    yield client, commit_oid, blob_uri, expected_bytes


async def test_restore_dry_run_does_not_mutate(client_with_resource):
    client, _root = client_with_resource
    v1 = (await client.post("/api/v1/snapshot/commit", json={"message": "v1"})).json()["result"]

    resp = await client.post(
        "/api/v1/snapshot/restore",
        json={
            "project_dir": "viking://resources",
            "source_commit": v1["commit_oid"],
            "dry_run": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    # Per VikingFS.restore contract, dry_run responses carry 'diff'.
    assert "diff" in result or result.get("result") == "noop"


async def test_show_commit_metadata(client_with_resource):
    client, _ = client_with_resource
    commit = (await client.post("/api/v1/snapshot/commit", json={"message": "meta"})).json()["result"]
    resp = await client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": commit["commit_oid"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    meta = body["result"]
    assert meta["oid"] == commit["commit_oid"]
    assert "tree" in meta and "message" in meta


async def test_show_blob_returns_binary_with_headers(client_with_resource_and_blob):
    """show?path=<file> must return raw bytes + X-Snapshot-* headers."""
    client, commit_oid, blob_uri, expected_bytes = client_with_resource_and_blob
    resp = await client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": commit_oid, "path": blob_uri},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/octet-stream")
    assert "x-snapshot-oid" in {k.lower() for k in resp.headers}
    assert "x-snapshot-size" in {k.lower() for k in resp.headers}
    assert int(resp.headers["x-snapshot-size"]) == len(expected_bytes)
    assert resp.content == expected_bytes


async def test_show_path_not_found_returns_404(client_with_resource):
    client, _ = client_with_resource
    commit = (await client.post("/api/v1/snapshot/commit", json={"message": "for 404"})).json()["result"]
    resp = await client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": commit["commit_oid"], "path": "viking://resources/does_not_exist.txt"},
    )
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


# ---------------------------------------------------------------------------
# restore (apply) — forward-commit chain + reindex hook + concurrent-commit 409
# ---------------------------------------------------------------------------


async def test_restore_apply_advances_head_with_forward_commit(client_with_resource_and_blob, service):
    """End-to-end restore (dry_run=False) over HTTP: verify forward-commit
    semantics — the new commit's parent is the previous HEAD, NOT the source
    commit, and the workspace bytes match the source.
    """
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    client, c1_oid, blob_uri, v1_bytes = client_with_resource_and_blob
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    # Overwrite the blob and commit a second snapshot (c2 becomes new HEAD).
    v2_bytes = b"v2 modified content\n"
    await service.viking_fs.write_file(blob_uri, v2_bytes, ctx=ctx)
    c2_resp = await client.post("/api/v1/snapshot/commit", json={"message": "v2"})
    assert c2_resp.status_code == 200, c2_resp.text
    c2 = c2_resp.json()["result"]
    assert c2["result"] == "created"
    c2_oid = c2["commit_oid"]

    # Apply restore back to c1 over the whole resources scope.
    restore_resp = await client.post(
        "/api/v1/snapshot/restore",
        json={
            "project_dir": "viking://resources",
            "source_commit": c1_oid,
        },
    )
    assert restore_resp.status_code == 200, restore_resp.text
    body = restore_resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["result"] == "applied"
    assert result["source_commit"] == c1_oid
    assert result["parent_commit"] == c2_oid  # forward-commit: parent = old HEAD
    new_oid = result["new_commit_oid"]
    assert new_oid not in (c1_oid, c2_oid)

    # HEAD must point at the new commit, whose parent[0] == c2 (NOT c1).
    head_resp = await client.get("/api/v1/snapshot/show", params={"target_ref": "main"})
    assert head_resp.status_code == 200
    head = head_resp.json()["result"]
    assert head["oid"] == new_oid
    assert head["parents"] == [c2_oid]

    # The blob in the restored commit must equal v1.
    show_resp = await client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": new_oid, "path": blob_uri},
    )
    assert show_resp.status_code == 200
    assert show_resp.content == v1_bytes


async def test_restore_without_project_dir_restores_full_account_tree(client_with_resource_and_blob, service):
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    client, c1_oid, blob_uri, v1_bytes = client_with_resource_and_blob
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    extra_uri = "viking://resources/full_restore_new.txt"

    await service.viking_fs.write_file(blob_uri, b"v2 modified content\n", ctx=ctx)
    await service.viking_fs.write_file(extra_uri, b"new at v2\n", ctx=ctx)
    c2_resp = await client.post("/api/v1/snapshot/commit", json={"message": "v2 full restore setup"})
    assert c2_resp.status_code == 200, c2_resp.text
    c2_oid = c2_resp.json()["result"]["commit_oid"]

    restore_resp = await client.post(
        "/api/v1/snapshot/restore",
        json={"source_commit": c1_oid},
    )
    assert restore_resp.status_code == 200, restore_resp.text
    result = restore_resp.json()["result"]
    assert result["result"] == "applied"
    assert result["source_commit"] == c1_oid
    assert result["parent_commit"] == c2_oid
    assert blob_uri.removeprefix("viking://") in result["written_paths"]
    assert extra_uri.removeprefix("viking://") in result["deleted_paths"]

    show_resp = await client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": result["new_commit_oid"], "path": blob_uri},
    )
    assert show_resp.status_code == 200
    assert show_resp.content == v1_bytes

    missing_resp = await client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": result["new_commit_oid"], "path": extra_uri},
    )
    assert missing_resp.status_code == 404


async def test_restore_apply_triggers_reindex_hook(client_with_resource_and_blob, service, monkeypatch):
    """Verify the HTTP restore path actually invokes the vector-reindex
    scheduler — protects the chain router -> viking_fs.restore -> _schedule_vector_rebuild.
    """
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier
    import openviking.service.reindex_executor as reindex_mod

    client, c1_oid, blob_uri, _v1 = client_with_resource_and_blob
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    calls: list[str] = []

    class _SpyExecutor:
        async def execute(self, *, uri, mode, wait, ctx):
            calls.append(uri)
            return {"ok": True}

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: _SpyExecutor())

    # Mutate, commit v2, then restore back to c1 — must produce a reindex call.
    await service.viking_fs.write_file(blob_uri, b"v2-bytes\n", ctx=ctx)
    await client.post("/api/v1/snapshot/commit", json={"message": "v2"})

    restore_resp = await client.post(
        "/api/v1/snapshot/restore",
        json={"project_dir": "viking://resources", "source_commit": c1_oid},
    )
    assert restore_resp.status_code == 200, restore_resp.text
    assert restore_resp.json()["result"]["result"] == "applied"

    # The restore reindex now runs in a tracked background task; poll a little
    # to let start() + the gathered rebuild coroutines flush.
    import asyncio
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.02)

    assert calls, "expected at least one reindex call after restore apply"


async def test_restore_delete_removes_orphaned_vectors(client_with_resource_and_blob, service, monkeypatch):
    """Restoring to a revision that predates a file must purge that file's
    vectors, not merely skip them.

    ReindexExecutor only upserts from on-disk content and never deletes, so a
    file removed by the restore would otherwise leave orphaned vectors behind.
    viking_fs.restore must route deleted source paths to the executor's
    level-precise delete (DETAIL).
    """
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier
    import openviking.service.reindex_executor as reindex_mod

    client, c1_oid, _blob_uri, _v1 = client_with_resource_and_blob
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    deleted_calls: list[tuple] = []

    class _SpyExecutor:
        async def execute(self, *, uri, mode, wait, ctx):
            return {"ok": True}

        async def reindex_directory_marker(self, *, dir_uri, level, ctx):
            return None

        async def delete_uri_level(self, *, uri, level, ctx):
            deleted_calls.append((uri, int(level)))
            return 0

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: _SpyExecutor())

    # Add a brand-new file that does not exist at c1, then commit v2.
    new_uri = "viking://resources/restore_delete_fixture.txt"
    await service.viking_fs.write_file(new_uri, b"only-exists-at-v2\n", ctx=ctx)
    await client.post("/api/v1/snapshot/commit", json={"message": "v2 add file"})

    # Restore back to c1: the new file must be deleted, and its vectors purged.
    restore_resp = await client.post(
        "/api/v1/snapshot/restore",
        json={"project_dir": "viking://resources", "source_commit": c1_oid},
    )
    assert restore_resp.status_code == 200, restore_resp.text
    assert restore_resp.json()["result"]["result"] == "applied"

    import asyncio
    for _ in range(100):
        if (new_uri, 2) in deleted_calls:
            break
        await asyncio.sleep(0.02)

    assert (new_uri, 2) in deleted_calls, (
        f"deleted file's DETAIL vector must be purged; got {deleted_calls!r}"
    )



async def test_restore_concurrent_commit_returns_409(client_with_resource_and_blob, service, monkeypatch):
    """Force the underlying git CAS swap to raise GitConcurrentCommitError
    and verify the HTTP layer maps it to 409 with CONFLICT code.
    """
    from openviking.pyagfs.exceptions import GitConcurrentCommitError

    client, c1_oid, _blob_uri, _v1 = client_with_resource_and_blob

    async def _raise_conflict(self, *, message=None, **kwargs):
        raise GitConcurrentCommitError("git ref refs/heads/main changed under us")

    from openviking.storage.viking_fs import VikingFS

    monkeypatch.setattr(VikingFS, "restore", _raise_conflict)

    resp = await client.post(
        "/api/v1/snapshot/restore",
        json={"project_dir": "viking://resources", "source_commit": c1_oid},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "CONFLICT"


async def test_restore_partial_writeback_surfaces_structured_error(
    client_with_resource_and_blob, service, monkeypatch
):
    """When VikingFS.restore raises GitRestoreWritebackPartialError, the
    router must turn it into an OpenVikingError(code='RESTORE_WRITEBACK_PARTIAL')
    whose ``details`` carry the full payload (new_commit_oid, failed paths,
    task_id) — NOT a generic InternalError.
    """
    from openviking.pyagfs.exceptions import GitRestoreWritebackPartialError

    client, c1_oid, _blob_uri, _v1 = client_with_resource_and_blob

    async def _raise_partial(self, *, message=None, **kwargs):
        payload = {
            "new_commit_oid": "f" * 40,
            "source_commit": c1_oid,
            "parent_commit": "e" * 40,
            "written": 1,
            "deleted": 0,
            "unchanged": 0,
            "written_paths": ["resources/ok.md"],
            "deleted_paths": [],
            "failed_writes": [("resources/bad.md", "vfs write boom")],
            "failed_deletes": [],
        }
        exc = GitRestoreWritebackPartialError(
            "restore writeback partial: 1 write(s) and 0 delete(s) failed",
            payload=payload,
        )
        exc.task_id = "task-fixture-xyz"
        raise exc

    from openviking.storage.viking_fs import VikingFS

    monkeypatch.setattr(VikingFS, "restore", _raise_partial)

    resp = await client.post(
        "/api/v1/snapshot/restore",
        json={"project_dir": "viking://resources", "source_commit": c1_oid},
    )
    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "RESTORE_WRITEBACK_PARTIAL"
    details = body["error"]["details"]
    assert details["new_commit_oid"] == "f" * 40
    assert details["task_id"] == "task-fixture-xyz"
    assert details["written_paths"] == ["resources/ok.md"]
    # failed_writes round-trips as list-of-list under JSON since to_dict
    # serialises the tuples that way.
    assert details["failed_writes"] == [["resources/bad.md", "vfs write boom"]]


async def test_restore_rejects_unknown_field_per_pydantic_forbid(client_with_resource):
    """Pydantic ConfigDict(extra='forbid') on RestoreRequest must reject typo'd fields.

    The OpenViking error mapper rewrites FastAPI's default 422 into HTTP 400
    with code INVALID_ARGUMENT — that's the contract callers see.
    """
    client, _ = client_with_resource
    commit = (await client.post("/api/v1/snapshot/commit", json={"message": "v"})).json()["result"]
    resp = await client.post(
        "/api/v1/snapshot/restore",
        json={
            "project_dir": "viking://resources",
            "source_commit": commit["commit_oid"],
            "dryRun": True,  # typo: should be dry_run
        },
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    # The offending field name must surface in the error so the user can fix it.
    assert "dryRun" in body["error"]["message"]
