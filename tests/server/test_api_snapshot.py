# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end tests for /api/v1/snapshot/*."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

import openviking.service.reindex_executor as reindex_mod
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.app import create_app
from openviking.server.auth import get_request_context
from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import AclMode
from openviking_cli.exceptions import (
    InvalidArgumentError,
    InvalidURIError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_api_resources import _wait_task_terminal

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


@pytest_asyncio.fixture(scope="function")
async def snapshot_router_client():
    """Production app without running its service-initializing lifespan."""
    app = create_app(
        config=ServerConfig(),
        service=SimpleNamespace(sessions=None),
    )

    async def request_context_override():
        return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    app.dependency_overrides[get_request_context] = request_context_override

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_log_returns_recent_commits(client, service):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    uri = "viking://resources/new-snapshot.md"
    message = "new file snapshot"
    await service.viking_fs.write_file(uri, "new content", ctx=ctx)
    committed = await client.post(
        "/api/v1/snapshot/commit",
        json={
            "message": message,
            "paths": [uri],
            "author_name": "Snapshot Writer",
            "author_email": "writer@example.com",
        },
    )
    assert committed.status_code == 200, committed.text
    result = committed.json()["result"]
    assert result["result"] == "created"
    oid = result["commit_oid"]

    logged = await client.get(
        "/api/v1/snapshot/log", params={"branch": "main", "paths": uri, "limit": 1}
    )
    assert logged.status_code == 200, logged.text
    [entry] = logged.json()["result"]
    assert entry["oid"] == oid
    assert entry["message"] == message

    shown = await client.get("/api/v1/snapshot/show", params={"target_ref": oid})
    assert shown.status_code == 200, shown.text
    meta = shown.json()["result"]
    assert meta["oid"] == oid
    assert meta["message"] == message
    assert len(meta["tree"]) == 40
    assert meta["author"]["name"] == "Snapshot Writer"
    assert meta["author"]["email"] == "writer@example.com"


@pytest.mark.parametrize("guarded", [False, True])
async def test_native_file_versions_and_restore_without_compile_config(
    client_with_resource_and_blob, service, guarded
):
    """Ordinary files use commit cursors and restore snapshots without a Compile registration."""
    client, first_oid, uri, first_bytes = client_with_resource_and_blob
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    await service.viking_fs.write_file(uri, b"second version", ctx=ctx)
    second = await service.viking_fs.commit(message="second file version", paths=[uri], ctx=ctx)
    second_oid = second["commit_oid"]

    response = await client.get("/api/v1/snapshot/versions", params={"uri": uri, "limit": 1})
    assert response.status_code == 200, response.text
    page = response.json()["result"]
    assert set(page) == {"uri", "versions", "next_before_snapshot"}
    assert page["versions"][0]["oid"] == second_oid
    assert page["next_before_snapshot"] == second_oid
    previous = await client.get(
        "/api/v1/snapshot/versions",
        params={"uri": uri, "limit": 1, "before_snapshot": second_oid},
    )
    assert previous.status_code == 200, previous.text
    assert previous.json()["result"]["versions"][0]["oid"] == first_oid

    payload = {"path": uri, "restore_commit_id": first_oid}
    if guarded:
        payload["current_commit_id"] = second_oid
    dry_run = await client.post("/api/v1/snapshot/restore", json={**payload, "dry_run": True})
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["result"]["result"] == "dry_run"
    assert await service.viking_fs.read_file_bytes(uri, ctx=ctx) == b"second version"
    restored = await client.post("/api/v1/snapshot/restore", json=payload)
    assert restored.status_code == 200, restored.text
    result = restored.json()["result"]
    assert set(result) == {"uri", "snapshot_id", "result"}
    assert result["result"] == "created"
    assert await service.viking_fs.read_file_bytes(uri, ctx=ctx) == first_bytes
    stale = await client.post(
        "/api/v1/snapshot/restore", json={**payload, "current_commit_id": second_oid}
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["details"]["resource"] == "VERSION_CONFLICT"


async def test_log_empty_repo_returns_404(client_with_no_repo):
    """When the branch has no commits, /log should surface 404."""
    client = client_with_no_repo
    resp = await client.get("/api/v1/snapshot/log", params={"branch": "main", "limit": 5})
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


async def test_log_value_error_is_mapped(monkeypatch):
    from openviking.server.routers import snapshot

    fake_service = SimpleNamespace(
        fs=SimpleNamespace(
            log=AsyncMock(
                side_effect=ValueError("git tree path cannot be the account root: 'viking://'")
            )
        )
    )
    monkeypatch.setattr(snapshot, "get_service", lambda: fake_service)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with pytest.raises((InvalidArgumentError, InvalidURIError)):
        await snapshot.log(branch="main", limit=5, paths=["viking://"], _ctx=ctx)


async def test_log_accepts_32_raw_path_parameters(snapshot_router_client, monkeypatch):
    from openviking.server.routers import snapshot

    log_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        snapshot,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(log=log_mock)),
    )
    paths = [f"viking://resources/{index}.md" for index in range(32)]
    response = await snapshot_router_client.get("/api/v1/snapshot/log", params={"paths": paths})
    assert response.status_code == 200
    assert log_mock.await_args.kwargs["paths"] == paths

    rejected = await snapshot_router_client.get(
        "/api/v1/snapshot/log", params={"paths": [*paths, paths[0]]}
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "INVALID_ARGUMENT"
    log_mock.assert_awaited_once()


async def test_log_scan_limit_error_maps_to_invalid_argument(monkeypatch):
    from openviking.pyagfs.exceptions import AGFSInvalidOperationError
    from openviking.server.routers import snapshot

    fake_service = SimpleNamespace(
        fs=SimpleNamespace(
            log=AsyncMock(
                side_effect=AGFSInvalidOperationError(
                    "snapshot log scan limit exceeded: scanned 1000/1000 commits"
                )
            )
        )
    )
    monkeypatch.setattr(snapshot, "get_service", lambda: fake_service)
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    with pytest.raises(InvalidArgumentError, match="scan limit exceeded"):
        await snapshot.log(
            branch="main",
            limit=1,
            paths=["viking://resources/missing.md"],
            _ctx=ctx,
        )


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


async def test_restore_acl_and_reindex_identity(client_with_resource, service, monkeypatch):
    _, _root = client_with_resource
    admin = RequestContext(user=service.user, role=Role.ADMIN)
    await service.runtime_config_manager.patch_account(admin.account_id, {"acl": {"enabled": True}})
    writer = RequestContext(
        user=UserIdentifier(admin.account_id, "snapshot_writer"),
        role=Role.USER,
        group_ids=("engineering",),
        actor_peer_id="snapshot-peer",
    )
    reader = RequestContext(
        user=UserIdentifier(admin.account_id, "snapshot_reader"),
        role=Role.USER,
    )
    root = "viking://resources/snapshot_acl_restore"
    file_uri = f"{root}/document.md"
    extra_uri = f"{root}/remove.md"

    await service.fs.mkdir(root, ctx=admin)
    await service.fs.ensure_directory_index(
        root, admin, fallback_abstract="Snapshot ACL restore", wait=True
    )
    await service.fs.set_acl(
        root,
        [
            {"principal": "user:snapshot_writer", "level": "write"},
            {"principal": "user:snapshot_reader", "level": "read"},
        ],
        ctx=admin,
        acl_mode=AclMode.RESTRICTED,
    )
    await service.fs.write(file_uri, content="v1", mode="create", wait=True, ctx=admin)
    v1 = await service.fs.commit(message="acl restore v1", paths=[root], ctx=writer)

    assert await service.fs.show(v1["commit_oid"], path=file_uri, ctx=reader)
    assert await service.fs.log(paths=[root], ctx=reader)
    snapshot = await service.fs.commit(message="reader snapshot", paths=[root], ctx=reader)
    assert snapshot["commit_oid"] == v1["commit_oid"]

    await service.fs.write(file_uri, content="v2", mode="replace", wait=True, ctx=writer)
    await service.fs.commit(message="acl restore v2", paths=[root], ctx=writer)

    with pytest.raises(PermissionDeniedError):
        await service.fs.restore(
            project_dir=root,
            source_commit=v1["commit_oid"],
            dry_run=True,
            ctx=reader,
        )

    write_plan = await service.fs.restore(
        project_dir=root,
        source_commit=v1["commit_oid"],
        dry_run=True,
        ctx=writer,
    )
    assert [item["path"] for item in write_plan["diff"]["to_write"]] == ["document.md"]

    await service.fs.write(extra_uri, content="remove", mode="create", wait=True, ctx=admin)
    await service.fs.commit(message="acl restore delete", paths=[root], ctx=admin)
    delete_plan = await service.fs.restore(
        project_dir=root,
        source_commit=v1["commit_oid"],
        dry_run=True,
        ctx=writer,
    )
    assert delete_plan["diff"]["to_delete"] == ["remove.md"]
    assert await service.fs.read(file_uri, ctx=writer) == "v2"

    reindex_contexts: list[RequestContext] = []

    class _SpyExecutor:
        async def execute(self, *, uri, mode, wait, ctx):
            reindex_contexts.append(ctx)
            return {"ok": True}

        async def execute_in_current_task(self, *, uri, mode, ctx):
            reindex_contexts.append(ctx)
            return {"ok": True}

        async def reindex_directory_marker(self, *, dir_uri, level, ctx):
            reindex_contexts.append(ctx)

        async def delete_uri_level(self, *, uri, level, ctx):
            reindex_contexts.append(ctx)
            return 0

    def factory():
        return _SpyExecutor()

    monkeypatch.setattr(reindex_mod, "ReindexExecutor", factory)
    result = await service.fs.restore(
        project_dir=root,
        source_commit=v1["commit_oid"],
        ctx=writer,
    )
    assert result["result"] == "applied"

    for _ in range(100):
        if reindex_contexts:
            break
        await asyncio.sleep(0.02)

    assert reindex_contexts
    assert all(ctx.role == Role.USER for ctx in reindex_contexts)
    assert all(ctx.group_ids == ("engineering",) for ctx in reindex_contexts)
    assert all(ctx.actor_peer_id == "snapshot-peer" for ctx in reindex_contexts)
    assert all(ctx.bypass_acl for ctx in reindex_contexts)

    await service.fs.set_acl(file_uri, [], ctx=admin, acl_mode=AclMode.RESTRICTED)
    with pytest.raises(PermissionDeniedError):
        await service.fs.commit(message="unreadable descendant", paths=[root], ctx=reader)


@pytest.mark.parametrize("use_branch", [False, True])
async def test_show_blob_returns_binary_with_headers(client_with_resource_and_blob, use_branch):
    """show?path=<file> must return raw bytes + X-Snapshot-* headers."""
    client, commit_oid, blob_uri, expected_bytes = client_with_resource_and_blob
    resp = await client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": "main" if use_branch else commit_oid, "path": blob_uri},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/octet-stream")
    assert "x-snapshot-oid" in {k.lower() for k in resp.headers}
    assert "x-snapshot-size" in {k.lower() for k in resp.headers}
    assert int(resp.headers["x-snapshot-size"]) == len(expected_bytes)
    assert resp.content == expected_bytes
    metadata = await client.get("/api/v1/snapshot/show", params={"target_ref": commit_oid})
    assert metadata.status_code == 200
    assert resp.headers["x-snapshot-commit-oid"] == commit_oid
    assert (
        int(resp.headers["x-snapshot-time-seconds"])
        == metadata.json()["result"]["committer"]["time_seconds"]
    )


async def test_show_blob_can_hide_memory_fields(snapshot_router_client, monkeypatch):
    from openviking.server.routers import snapshot

    stored = b'visible content\n\n<!-- MEMORY_FIELDS\n{"version": 2}\n-->'
    show_mock = AsyncMock(return_value={"oid": "a" * 40, "size": len(stored), "bytes": stored})
    show_mock.return_value.update(commit_oid="b" * 40, commit_time_seconds=1793951444)
    monkeypatch.setattr(
        snapshot,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(show_blob_raw=show_mock)),
    )

    response = await snapshot_router_client.get(
        "/api/v1/snapshot/show",
        params={
            "target_ref": "b" * 40,
            "path": "viking://user/default/memories/experiences/example.md",
            "raw": "false",
        },
    )

    assert response.status_code == 200, response.text
    assert response.content == b"visible content"
    assert response.headers["x-snapshot-size"] == str(len(response.content))
    assert response.headers["x-snapshot-oid"] == "a" * 40
    assert response.headers["x-snapshot-commit-oid"] == "b" * 40
    assert response.headers["x-snapshot-time-seconds"] == "1793951444"


async def test_show_path_not_found_returns_404(client_with_resource):
    client, _ = client_with_resource
    commit = (await client.post("/api/v1/snapshot/commit", json={"message": "for 404"})).json()[
        "result"
    ]
    resp = await client.get(
        "/api/v1/snapshot/show",
        params={
            "target_ref": commit["commit_oid"],
            "path": "viking://resources/does_not_exist.txt",
        },
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["details"] == {
        "type": "git_blob",
        "resource": "viking://resources/does_not_exist.txt",
        "reason": "path_not_found",
        "deleted_file": False,
    }


@pytest.mark.parametrize("was_directory", [False, True], ids=["large-file", "directory"])
async def test_show_deleted_path_reports_whether_first_parent_had_a_file(
    client_with_resource, service, was_directory
):
    client, _ = client_with_resource
    path = "viking://resources/recreated_wiki.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    vfs = service.viking_fs
    if was_directory:
        await vfs.mkdir(path, ctx=ctx)
        await vfs.write_file(f"{path}/page.md", b"a directory child\n", ctx=ctx)
    else:
        await vfs.write_file(path, b"large Markdown file\n" * 100_001, ctx=ctx)
    await vfs.commit(message="original path", paths=[path], ctx=ctx)

    await vfs.rm(path, recursive=was_directory, ctx=ctx)
    deletion = await vfs.commit(message="removed path", paths=[path], ctx=ctx)
    await vfs.write_file(path, b"recreated as file\n", ctx=ctx)
    await vfs.commit(message="recreated file", paths=[path], ctx=ctx)

    response = await client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": deletion["commit_oid"], "path": path, "raw": "false"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["details"] == {
        "type": "git_blob",
        "resource": path,
        "reason": "path_not_found",
        "deleted_file": not was_directory,
    }


async def test_show_missing_snapshot_object_does_not_report_missing_tree_path(
    snapshot_router_client, monkeypatch
):
    from openviking.server.routers import snapshot

    path = "viking://resources/tracked.txt"
    show_mock = AsyncMock(side_effect=AGFSNotFoundError("object not found"))
    monkeypatch.setattr(
        snapshot,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(show_blob_raw=show_mock)),
    )

    response = await snapshot_router_client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": "b" * 40, "path": path},
    )
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["details"] == {"type": "git_blob", "resource": path}


async def test_diff_returns_unified_diff_for_path(client_with_resource, service):
    client, _ = client_with_resource
    path = "viking://resources/snapshot_diff_fixture.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    await service.viking_fs.write_file(path, b"# Title\n\nold line\n", ctx=ctx)
    from_commit = (
        await client.post(
            "/api/v1/snapshot/commit",
            json={"message": "snapshot diff v1", "paths": [path]},
        )
    ).json()["result"]["commit_oid"]

    await service.viking_fs.write_file(path, b"# Title\n\nnew line\n", ctx=ctx)
    to_commit = (
        await client.post(
            "/api/v1/snapshot/commit",
            json={"message": "snapshot diff v2", "paths": [path]},
        )
    ).json()["result"]["commit_oid"]

    response = await client.get(
        "/api/v1/snapshot/diff",
        params={"path": path, "from": from_commit, "to": to_commit},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["path"] == path
    assert result["from_commit"] == from_commit
    assert result["to_commit"] == to_commit
    assert result["change_type"] == "modified"
    assert "-old line" in result["diff_text"]
    assert "+new line" in result["diff_text"]

    response = await client.get(
        "/api/v1/snapshot/diff-paths",
        params={"paths": "viking://resources", "from": from_commit, "to": to_commit, "limit": 1},
    )
    assert response.status_code == 200, response.text
    page = response.json()["result"]
    assert page["changes"] == [{"uri": path, "change_type": "modified"}]
    assert page["total"] == 1 and page["has_more"] is False


async def test_diff_supports_first_version_without_from_commit(client_with_resource, service):
    client, _ = client_with_resource
    path = "viking://resources/snapshot_diff_added_fixture.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    await service.viking_fs.write_file(path, b"first version\n", ctx=ctx)
    to_commit = (
        await client.post(
            "/api/v1/snapshot/commit",
            json={"message": "snapshot diff added", "paths": [path]},
        )
    ).json()["result"]["commit_oid"]

    response = await client.get(
        "/api/v1/snapshot/diff",
        params={"path": path, "to": to_commit},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["from_commit"] == ""
    assert result["to_commit"] == to_commit
    assert result["change_type"] == "added"
    assert "+first version" in result["diff_text"]


async def test_diff_reports_missing_final_newline(client_with_resource, service):
    client, _ = client_with_resource
    path = "viking://resources/snapshot_diff_newline_fixture.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    await service.viking_fs.write_file(path, b"same line\n", ctx=ctx)
    from_commit = (
        await client.post(
            "/api/v1/snapshot/commit",
            json={"message": "snapshot diff newline v1", "paths": [path]},
        )
    ).json()["result"]["commit_oid"]

    await service.viking_fs.write_file(path, b"same line", ctx=ctx)
    to_commit = (
        await client.post(
            "/api/v1/snapshot/commit",
            json={"message": "snapshot diff newline v2", "paths": [path]},
        )
    ).json()["result"]["commit_oid"]

    response = await client.get(
        "/api/v1/snapshot/diff",
        params={"path": path, "from": from_commit, "to": to_commit},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["change_type"] == "modified"
    assert "-same line" in result["diff_text"]
    assert "+same line" in result["diff_text"]
    assert "\\ No newline at end of file" in result["diff_text"]


async def test_diff_reports_deleted_path(client_with_resource, service):
    client, _ = client_with_resource
    path = "viking://resources/snapshot_diff_deleted_fixture.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    await service.viking_fs.write_file(path, b"deleted content\n", ctx=ctx)
    from_commit = (
        await client.post(
            "/api/v1/snapshot/commit",
            json={"message": "snapshot diff deleted v1", "paths": [path]},
        )
    ).json()["result"]["commit_oid"]

    await service.viking_fs.rm(path, ctx=ctx)
    to_commit = (
        await client.post(
            "/api/v1/snapshot/commit",
            json={"message": "snapshot diff deleted v2", "paths": [path]},
        )
    ).json()["result"]["commit_oid"]

    response = await client.get(
        "/api/v1/snapshot/diff",
        params={"path": path, "from": from_commit, "to": to_commit},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["change_type"] == "deleted"
    assert "-deleted content" in result["diff_text"]


async def test_diff_reports_unchanged_path(client_with_resource, service):
    client, _ = client_with_resource
    path = "viking://resources/snapshot_diff_unchanged_fixture.md"
    other_path = "viking://resources/snapshot_diff_other_fixture.md"
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    await service.viking_fs.write_file(path, b"unchanged content\n", ctx=ctx)
    from_commit = (
        await client.post(
            "/api/v1/snapshot/commit",
            json={"message": "snapshot diff unchanged v1", "paths": [path]},
        )
    ).json()["result"]["commit_oid"]

    await service.viking_fs.write_file(other_path, b"advance snapshot\n", ctx=ctx)
    to_commit = (
        await client.post(
            "/api/v1/snapshot/commit",
            json={"message": "snapshot diff unchanged v2", "paths": [other_path]},
        )
    ).json()["result"]["commit_oid"]

    response = await client.get(
        "/api/v1/snapshot/diff",
        params={"path": path, "from": from_commit, "to": to_commit},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["change_type"] == "unchanged"
    assert result["diff_text"] == ""


# ---------------------------------------------------------------------------
# restore (apply) — forward-commit chain + reindex hook + concurrent-commit 409
# ---------------------------------------------------------------------------


async def test_restore_apply_advances_head_with_forward_commit(
    client_with_resource_and_blob, service
):
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
            "message": "Restore earlier contents",
            "author_name": "Snapshot Restorer",
            "author_email": "restorer@example.com",
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
    assert head["message"] == "Restore earlier contents"
    assert head["author"]["name"] == "Snapshot Restorer"
    assert head["author"]["email"] == "restorer@example.com"

    # The blob in the restored commit must equal v1.
    show_resp = await client.get(
        "/api/v1/snapshot/show",
        params={"target_ref": new_oid, "path": blob_uri},
    )
    assert show_resp.status_code == 200
    assert show_resp.content == v1_bytes


async def test_restore_without_project_dir_restores_full_account_tree(
    client_with_resource_and_blob, service
):
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    client, c1_oid, blob_uri, v1_bytes = client_with_resource_and_blob
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    extra_uri = "viking://resources/full_restore_new.txt"

    await service.viking_fs.write_file(blob_uri, b"v2 modified content\n", ctx=ctx)
    await service.viking_fs.write_file(extra_uri, b"new at v2\n", ctx=ctx)
    c2_resp = await client.post(
        "/api/v1/snapshot/commit", json={"message": "v2 full restore setup"}
    )
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


async def test_restore_delete_removes_orphaned_vectors(client, service):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    root = "viking://resources/snapshot-vectors"
    restored_uri = f"{root}/restored.md"
    deleted_uri = f"{root}/deleted.md"

    async def write(uri, text):
        response = await client.post(
            "/api/v1/content/write",
            json={"uri": uri, "content": text, "mode": "create", "wait": True},
        )
        assert response.status_code == 200, response.text

    async def vectors():
        return await service.viking_fs.vector_store.get_l2_diff_records_by_uris(
            [restored_uri, deleted_uri], ctx=ctx
        )

    await write(restored_uri, "Initial snapshot body.")
    initial_vectors = await vectors()
    assert set(initial_vectors) == {restored_uri}
    assert initial_vectors[restored_uri]["md5"]
    original = await client.post(
        "/api/v1/snapshot/commit", json={"paths": [root], "message": "initial"}
    )
    assert original.status_code == 200, original.text
    oid = original.json()["result"]["commit_oid"]

    await write(restored_uri, "Later snapshot body.")
    await write(deleted_uri, "File created after the initial snapshot.")
    later = await client.post("/api/v1/snapshot/commit", json={"paths": [root], "message": "later"})
    assert later.status_code == 200, later.text
    later_vectors = await vectors()
    assert set(later_vectors) == {restored_uri, deleted_uri}
    assert later_vectors[restored_uri]["md5"] != initial_vectors[restored_uri]["md5"]

    response = await client.post(
        "/api/v1/snapshot/restore", json={"project_dir": root, "source_commit": oid}
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["result"] == "applied"
    task = await _wait_task_terminal(client, result["task_id"])
    assert task["status"] == "completed", task
    assert task["resource_id"] == root
    assert await service.viking_fs.read_file(restored_uri, ctx=ctx) == "Initial snapshot body."
    assert not await service.viking_fs.exists(deleted_uri, ctx=ctx)
    restored_vectors = await vectors()
    assert set(restored_vectors) == {restored_uri}
    assert restored_vectors[restored_uri]["md5"] == initial_vectors[restored_uri]["md5"]


async def test_restore_concurrent_commit_returns_409(
    client_with_resource_and_blob, service, monkeypatch
):
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


@pytest.mark.parametrize(
    "payload",
    [
        {"commit_ids": []},
        {"commit_ids": ["abc"]},
        {"commit_ids": [f"{n:040x}" for n in range(101)]},
        {"commit_ids": ["a" * 40], "branch": "main"},
    ],
)
async def test_delete_route_validation(snapshot_router_client, payload):
    response = await snapshot_router_client.post("/api/v1/snapshot/delete", json=payload)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_delete_route_normalizes_ids_and_preserves_head(
    client_with_resource_and_blob, service
):
    client, first, uri, _ = client_with_resource_and_blob
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    await service.viking_fs.write_file(uri, b"new tip", ctx=ctx)
    tip = (await service.viking_fs.commit(message="new tip", paths=[uri], ctx=ctx))["commit_oid"]
    url = "/api/v1/snapshot/delete"
    assert (await client.post(url, json={"commit_ids": [tip]})).status_code == 400
    assert (await client.post(url, json={"commit_ids": ["f" * 40]})).status_code == 404
    result = await client.post(url, json={"commit_ids": [first.upper(), first]})
    assert result.status_code == 200, result.text
    assert result.json()["result"]["result"] == "deleted"
    assert result.json()["result"]["deleted"] == [first]
    assert (
        await client.get("/api/v1/snapshot/show", params={"target_ref": first, "path": uri})
    ).status_code == 404


async def test_delete_snapshots_checks_write_permission_on_committed_file(
    client_with_resource_and_blob, service, monkeypatch
):
    from openviking.storage.acl import DirectAcl, EffectiveAcl

    _, _, uri, _ = client_with_resource_and_blob
    admin = RequestContext(user=service.user, role=Role.ADMIN)
    account = admin.account_id
    await service.runtime_config_manager.patch_account(account, {"acl": {"enabled": True}})
    writer = RequestContext(user=UserIdentifier(account, "writer"), role=Role.USER)
    reader = RequestContext(user=UserIdentifier(account, "reader"), role=Role.USER)
    permissions = DirectAcl.from_entries(
        [
            {"principal": "user:writer", "level": "write"},
            {"principal": "user:reader", "level": "read"},
        ]
    )
    acl = EffectiveAcl(AclMode.RESTRICTED, permissions, DirectAcl())
    monkeypatch.setattr(
        service.viking_fs.acl_manager, "resolve_many", AsyncMock(return_value={uri: acl})
    )
    snapshots = []
    for version in ("middle", "tip"):
        await service.viking_fs.write_file(uri, version.encode(), ctx=admin)
        snapshot = await service.fs.commit(message=version, paths=[uri], ctx=admin)
        snapshots.append(snapshot["commit_oid"])
    ids = snapshots[:1]
    with pytest.raises(PermissionDeniedError):
        await service.fs.delete_snapshots(commit_ids=ids, ctx=reader)
    preview = await service.fs.delete_snapshots(commit_ids=ids, dry_run=True, ctx=writer)
    assert preview["changed_paths"] == {ids[0]: 1}
    assert "plan" not in preview
    assert (await service.fs.delete_snapshots(commit_ids=ids, ctx=writer))["deleted"] == ids
