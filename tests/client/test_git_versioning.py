"""End-to-end tests for the OpenViking.snapshot namespace.

These exercise the user-facing namespace path:
OpenViking -> LocalClient -> FSService -> VikingFS -> RAGFSBindingClient -> Rust GitService.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import NamedTuple, Tuple
from unittest.mock import MagicMock

import pytest

ragfs_python = pytest.importorskip("ragfs_python")

from openviking.async_client import AsyncOpenViking
from openviking.client.local import LocalClient
from openviking.pyagfs.exceptions import AGFSNotSupportedError
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidURIError
from openviking.service.fs_service import FSService
from openviking.storage.viking_fs import VikingFS
from openviking.sync_client import SyncOpenViking
from openviking_cli.session.user_id import UserIdentifier


OID_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_AUTHOR_NAME = VikingFS._DEFAULT_GIT_AUTHOR_NAME


class ClientHarness(NamedTuple):
    client: SyncOpenViking
    async_client: AsyncOpenViking
    vfs: VikingFS
    ctx: RequestContext


def _make_ctx(account: str = "acct_t", user: str = "user1") -> RequestContext:
    return RequestContext(user=UserIdentifier(account, user), role=Role.ROOT)


def _write_workspace(tmp_root: Path) -> Tuple[Path, Path]:
    """Create ragfs config and backing localfs root for git-enabled tests."""
    fs_root = tmp_root / "fs"
    git_root = tmp_root / "git"
    fs_root.mkdir(parents=True, exist_ok=True)
    git_root.mkdir(parents=True, exist_ok=True)
    cfg = tmp_root / "ragfs.toml"
    cfg.write_text(
        f"""
[git]
enabled = true
backend = "local"
default_branch = "main"
author_name = "test-bot"
author_email = "test@example.com"

[git.local]
base_dir = "{git_root}"
"""
    )
    return cfg, fs_root


def _write_disabled_workspace(tmp_root: Path) -> Tuple[Path, Path]:
    fs_root = tmp_root / "fs"
    fs_root.mkdir(parents=True, exist_ok=True)
    cfg = tmp_root / "ragfs.toml"
    cfg.write_text(
        """
[git]
enabled = false
"""
    )
    return cfg, fs_root


def _build_binding_client(config_path: Path, fs_root: Path):
    client = ragfs_python.RAGFSBindingClient(git_config_path=str(config_path))
    client.mount("localfs", "/local", {"local_dir": str(fs_root)})
    return client


def _build_harness(config_path: Path, fs_root: Path) -> ClientHarness:
    from openviking.storage.transaction import init_lock_manager

    ctx = _make_ctx()
    binding_client = _build_binding_client(config_path, fs_root)
    init_lock_manager(binding_client)
    vfs = VikingFS(agfs=binding_client)

    fs_service = FSService()
    fs_service.set_dependencies(viking_fs=vfs)

    local_client = object.__new__(LocalClient)
    local_client._service = MagicMock()
    local_client._service.fs = fs_service
    local_client._ctx = ctx

    async_client = object.__new__(AsyncOpenViking)
    async_client._client = local_client
    async_client._initialized = True
    async_client._singleton_initialized = True
    async_client._snapshot = None

    sync_client = object.__new__(SyncOpenViking)
    sync_client._async_client = async_client
    sync_client._initialized = True
    sync_client._snapshot = None

    return ClientHarness(
        client=sync_client,
        async_client=async_client,
        vfs=vfs,
        ctx=ctx,
    )


@pytest.fixture
def workspace():
    root = Path(tempfile.mkdtemp(prefix="ov-client-git-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def git_harness(workspace) -> ClientHarness:
    from openviking.storage.transaction import reset_lock_manager

    cfg, fs_root = _write_workspace(workspace)
    try:
        yield _build_harness(cfg, fs_root)
    finally:
        reset_lock_manager()


@pytest.fixture
def git_disabled_harness(workspace) -> ClientHarness:
    from openviking.storage.transaction import reset_lock_manager

    cfg, fs_root = _write_disabled_workspace(workspace)
    try:
        yield _build_harness(cfg, fs_root)
    finally:
        reset_lock_manager()


async def test_write_commit_show_roundtrip(git_harness):
    await git_harness.vfs.write_file(
        "viking://resources/a.md",
        b"hello",
        ctx=git_harness.ctx,
    )

    commit = git_harness.client.snapshot.commit(
        message="initial",
        paths=["viking://resources/a.md"],
    )

    assert commit["result"] == "created"
    assert OID_RE.match(commit["commit_oid"])
    assert git_harness.client.snapshot.show(
        "main",
        path="viking://resources/a.md",
    ) == b"hello"


async def test_show_metadata_without_path(git_harness):
    await git_harness.vfs.write_file(
        "viking://resources/meta.md",
        b"metadata",
        ctx=git_harness.ctx,
    )
    commit = git_harness.client.snapshot.commit(
        message="metadata commit",
        paths=["viking://resources/meta.md"],
    )

    metadata = git_harness.client.snapshot.show("main")

    assert metadata["oid"] == commit["commit_oid"]
    assert metadata["message"].startswith("metadata commit")
    assert metadata["author"]["name"] == DEFAULT_AUTHOR_NAME
    assert metadata["parents"] == []


async def test_log_walks_parents(git_harness):
    commits = []
    for idx, body in enumerate((b"v1", b"v2", b"v3"), start=1):
        await git_harness.vfs.write_file(
            "viking://resources/log.md",
            body,
            ctx=git_harness.ctx,
        )
        commits.append(
            git_harness.client.snapshot.commit(
                message=f"c{idx}",
                paths=["viking://resources/log.md"],
            )
        )

    history = git_harness.client.snapshot.log(limit=10)
    limited = git_harness.client.snapshot.log(limit=2)

    assert [item["oid"] for item in history] == [
        commits[2]["commit_oid"],
        commits[1]["commit_oid"],
        commits[0]["commit_oid"],
    ]
    assert [item["oid"] for item in limited] == [
        commits[2]["commit_oid"],
        commits[1]["commit_oid"],
    ]


async def test_restore_reverts_file_and_advances_head(git_harness):
    await git_harness.vfs.write_file(
        "viking://resources/proj/a.md",
        b"v1",
        ctx=git_harness.ctx,
    )
    v1 = git_harness.client.snapshot.commit(
        message="v1",
        paths=["viking://resources/proj/a.md"],
    )

    await git_harness.vfs.write_file(
        "viking://resources/proj/a.md",
        b"v2",
        ctx=git_harness.ctx,
    )
    v2 = git_harness.client.snapshot.commit(
        message="v2",
        paths=["viking://resources/proj/a.md"],
    )

    restore = git_harness.client.snapshot.restore(
        project_dir="viking://resources/proj",
        source_commit=v1["commit_oid"],
    )

    assert restore["result"] == "applied"
    assert restore["source_commit"] == v1["commit_oid"]
    assert restore["parent_commit"] == v2["commit_oid"]
    assert restore["new_commit_oid"] != v2["commit_oid"]
    assert await git_harness.vfs.read(
        "viking://resources/proj/a.md",
        ctx=git_harness.ctx,
    ) == b"v1"
    assert git_harness.client.snapshot.show("main")["parents"] == [v2["commit_oid"]]


async def test_restore_dry_run_does_not_mutate(git_harness):
    await git_harness.vfs.write_file(
        "viking://resources/proj/a.md",
        b"v1",
        ctx=git_harness.ctx,
    )
    v1 = git_harness.client.snapshot.commit(
        message="v1",
        paths=["viking://resources/proj/a.md"],
    )
    await git_harness.vfs.write_file(
        "viking://resources/proj/a.md",
        b"v2",
        ctx=git_harness.ctx,
    )
    git_harness.client.snapshot.commit(
        message="v2",
        paths=["viking://resources/proj/a.md"],
    )
    before_log = git_harness.client.snapshot.log()

    dry_run = git_harness.client.snapshot.restore(
        project_dir="viking://resources/proj",
        source_commit=v1["commit_oid"],
        dry_run=True,
    )

    assert dry_run["result"] == "dry_run"
    assert any(item["path"] == "a.md" for item in dry_run["diff"]["to_write"])
    assert await git_harness.vfs.read(
        "viking://resources/proj/a.md",
        ctx=git_harness.ctx,
    ) == b"v2"
    assert len(git_harness.client.snapshot.log()) == len(before_log)


async def test_restore_internal_scope_rejected(git_harness):
    await git_harness.vfs.write_file(
        "viking://resources/a.md",
        b"content",
        ctx=git_harness.ctx,
    )
    commit = git_harness.client.snapshot.commit(
        message="commit",
        paths=["viking://resources/a.md"],
    )

    # Client-level calls cross FSService first; its URI validator rejects
    # internal scopes before VikingFS.restore can raise ValueError.
    with pytest.raises(InvalidURIError):
        git_harness.client.snapshot.restore(
            project_dir="viking://temp/x",
            source_commit=commit["commit_oid"],
        )


async def test_disabled_raises_not_supported(git_disabled_harness):
    with pytest.raises(AGFSNotSupportedError):
        git_disabled_harness.client.snapshot.commit(message="disabled")
    with pytest.raises(AGFSNotSupportedError):
        git_disabled_harness.client.snapshot.show("main")
    with pytest.raises(AGFSNotSupportedError):
        git_disabled_harness.client.snapshot.restore(
            project_dir="viking://resources/proj",
            source_commit="main",
        )
    with pytest.raises(AGFSNotSupportedError):
        git_disabled_harness.client.snapshot.log()


async def test_async_api_parity(git_harness):
    await git_harness.vfs.write_file(
        "viking://resources/async.md",
        b"async hello",
        ctx=git_harness.ctx,
    )

    commit = await git_harness.async_client.snapshot.commit(
        message="async initial",
        paths=["viking://resources/async.md"],
    )
    body = await git_harness.async_client.snapshot.show(
        "main",
        path="viking://resources/async.md",
    )

    assert commit["result"] == "created"
    assert OID_RE.match(commit["commit_oid"])
    assert body == b"async hello"
