"""End-to-end tests for VikingFS git commit/restore/show/log Python layer.

These exercise the full path: VikingFS.commit -> AsyncAGFSClient -> Rust
RAGFSBindingClient -> GitService, plus URI<->tree-path conversion and the
double-encryption invariant called out in the design doc.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Tuple

import pytest

from openviking.pyagfs.exceptions import (
    AGFSInvalidOperationError,
    AGFSNotFoundError,
    AGFSNotSupportedError,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier

ragfs_python = pytest.importorskip("ragfs_python")

# ----------------------------- helpers -----------------------------


def _make_ctx(account: str = "acct_t", user: str = "user1") -> RequestContext:
    return RequestContext(user=UserIdentifier(account, user), role=Role.ROOT)


def _write_workspace(tmp_root: Path) -> Tuple[Path, Path]:
    """Lay out an fs/ dir for localfs and a git/ dir for git objects; return
    (config_path, localfs_root)."""
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


def _build_client(config_path: Path, fs_root: Path):
    c = ragfs_python.RAGFSBindingClient(git_config_path=str(config_path))
    c.mount("localfs", "/local", {"local_dir": str(fs_root)})
    return c


# ----------------------------- fixtures -----------------------------


@pytest.fixture
def workspace():
    root = Path(tempfile.mkdtemp(prefix="ov-vfs-git-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def vfs(workspace):
    from openviking.storage.transaction import init_lock_manager, reset_lock_manager

    cfg, fs_root = _write_workspace(workspace)
    client = _build_client(cfg, fs_root)
    init_lock_manager(client)
    try:
        yield VikingFS(agfs=client)
    finally:
        reset_lock_manager()


@pytest.fixture
def vfs_disabled(workspace):
    from openviking.storage.transaction import init_lock_manager, reset_lock_manager

    cfg = workspace / "ragfs.toml"
    cfg.write_text(
        """
[git]
enabled = false
"""
    )
    fs_root = workspace / "fs"
    fs_root.mkdir()
    client = ragfs_python.RAGFSBindingClient(git_config_path=str(cfg))
    client.mount("localfs", "/local", {"local_dir": str(fs_root)})
    init_lock_manager(client)
    try:
        yield VikingFS(agfs=client)
    finally:
        reset_lock_manager()


# =========================================================================
# 1. URI <-> tree path
# =========================================================================


class TestUriToTreePath:
    def test_resources_uri(self, vfs):
        ctx = _make_ctx()
        assert vfs._uri_to_tree_path("viking://resources/a.md", ctx=ctx) == "resources/a.md"
        assert (
            vfs._uri_to_tree_path("viking://resources/proj_a/docs/a.md", ctx=ctx)
            == "resources/proj_a/docs/a.md"
        )

    def test_session_uri(self, vfs):
        # ``session`` is a virtual scope: it canonicalizes into the owning
        # user's sessions subtree, and the git tree path mirrors that real
        # storage layout (so commit/restore target the actual stored bytes).
        ctx = _make_ctx()
        assert vfs._uri_to_tree_path("viking://session", ctx=ctx) == "user/user1/sessions"

    def test_trailing_slash_kept_as_directory(self, vfs):
        # Normalization may strip trailing slash; this is acceptable
        ctx = _make_ctx()
        out = vfs._uri_to_tree_path("viking://resources/proj_a/", ctx=ctx)
        assert out.rstrip("/") == "resources/proj_a"

    def test_internal_scope_rejected(self, vfs):
        ctx = _make_ctx()
        for uri in (
            "viking://temp/x",
            "viking://queue/y",
            "viking://upload/z",
        ):
            with pytest.raises(ValueError):
                vfs._uri_to_tree_path(uri, ctx=ctx)

    def test_root_uri_rejected(self, vfs):
        ctx = _make_ctx()
        with pytest.raises(ValueError):
            vfs._uri_to_tree_path("viking://", ctx=ctx)

    def test_tree_path_to_uri_inverse(self, vfs):
        assert vfs._tree_path_to_uri("resources/a.md") == "viking://resources/a.md"
        assert vfs._tree_path_to_uri("/resources/a.md/") == "viking://resources/a.md"

    def test_tree_path_empty_rejected(self, vfs):
        with pytest.raises(ValueError):
            vfs._tree_path_to_uri("")


# =========================================================================
# 2. commit / show / log
# =========================================================================


@pytest.mark.asyncio
class TestCommitShowLog:
    async def test_commit_then_show_roundtrip(self, vfs):
        ctx = _make_ctx()
        await vfs.write_file("viking://resources/a.md", b"hello", ctx=ctx)
        resp = await vfs.commit(
            message="initial",
            paths=["viking://resources/a.md"],
            ctx=ctx,
        )
        assert resp["result"] == "created"
        assert resp["changed"] == 1
        assert len(resp["commit_oid"]) == 40

        # show with path -> bytes
        body = await vfs.show("main", path="viking://resources/a.md", ctx=ctx)
        assert body == b"hello"

        # show without path -> commit metadata
        meta = await vfs.show("main", ctx=ctx)
        assert meta["message"].startswith("initial")
        assert meta["oid"] == resp["commit_oid"]
        assert meta["parents"] == []
        assert meta["author"]["name"] == "viking-bot"

    async def test_commit_with_paths_none_enumerates_account(self, vfs):
        ctx = _make_ctx(account="acct_full")
        await vfs.write_file("viking://resources/a.md", b"a", ctx=ctx)
        await vfs.write_file("viking://resources/b.md", b"b", ctx=ctx)
        resp = await vfs.commit(message="all", ctx=ctx)
        assert resp["result"] == "created"
        assert resp["changed"] == 2

    async def test_log_walks_parent_chain(self, vfs):
        ctx = _make_ctx(account="acct_log")
        await vfs.write_file("viking://resources/a.md", b"v1", ctx=ctx)
        c1 = await vfs.commit(message="c1", paths=["viking://resources/a.md"], ctx=ctx)
        await vfs.write_file("viking://resources/a.md", b"v2", ctx=ctx)
        c2 = await vfs.commit(message="c2", paths=["viking://resources/a.md"], ctx=ctx)
        await vfs.write_file("viking://resources/a.md", b"v3", ctx=ctx)
        c3 = await vfs.commit(message="c3", paths=["viking://resources/a.md"], ctx=ctx)

        history = await vfs.log(limit=10, ctx=ctx)
        oids = [h["oid"] for h in history]
        assert oids == [c3["commit_oid"], c2["commit_oid"], c1["commit_oid"]]

        limited = await vfs.log(limit=2, ctx=ctx)
        assert [h["oid"] for h in limited] == [c3["commit_oid"], c2["commit_oid"]]

    async def test_show_missing_branch_raises(self, vfs):
        ctx = _make_ctx(account="acct_missing")
        with pytest.raises(AGFSNotFoundError):
            await vfs.show("main", ctx=ctx)


# =========================================================================
# 3. restore
# =========================================================================


@pytest.mark.asyncio
class TestRestore:
    async def test_restore_reverts_file_and_advances_head(self, vfs):
        ctx = _make_ctx(account="acct_r")
        await vfs.write_file("viking://resources/proj/a.md", b"v1", ctx=ctx)
        c1 = await vfs.commit(message="v1", paths=["viking://resources/proj/a.md"], ctx=ctx)

        await vfs.write_file("viking://resources/proj/a.md", b"v2", ctx=ctx)
        c2 = await vfs.commit(message="v2", paths=["viking://resources/proj/a.md"], ctx=ctx)

        result = await vfs.restore(
            project_dir="viking://resources/proj",
            source_commit=c1["commit_oid"],
            ctx=ctx,
        )
        assert result["result"] == "applied"
        assert result["source_commit"] == c1["commit_oid"]
        assert result["parent_commit"] == c2["commit_oid"]
        assert result["new_commit_oid"] != c2["commit_oid"]
        assert "resources/proj/a.md" in result["written_paths"]

        # File reverted via VFS
        body = await vfs.read("viking://resources/proj/a.md", ctx=ctx)
        assert body == b"v1"

        # HEAD moved forward (NOT back to c1)
        head = await vfs.show("main", ctx=ctx)
        assert head["oid"] == result["new_commit_oid"]
        assert head["parents"] == [c2["commit_oid"]]

    async def test_restore_dry_run_does_not_mutate(self, vfs):
        ctx = _make_ctx(account="acct_dry")
        await vfs.write_file("viking://resources/proj/a.md", b"v1", ctx=ctx)
        c1 = await vfs.commit(message="v1", paths=["viking://resources/proj/a.md"], ctx=ctx)
        await vfs.write_file("viking://resources/proj/a.md", b"v2", ctx=ctx)
        await vfs.commit(message="v2", paths=["viking://resources/proj/a.md"], ctx=ctx)

        result = await vfs.restore(
            project_dir="viking://resources/proj",
            source_commit=c1["commit_oid"],
            dry_run=True,
            ctx=ctx,
        )
        assert result["result"] == "dry_run"
        assert any(item["path"] == "a.md" for item in result["diff"]["to_write"])

        body = await vfs.read("viking://resources/proj/a.md", ctx=ctx)
        assert body == b"v2"

    async def test_restore_internal_scope_rejected(self, vfs):
        ctx = _make_ctx(account="acct_inv")
        with pytest.raises(ValueError):
            await vfs.restore(
                project_dir="viking://temp/xx",
                source_commit="main",
                ctx=ctx,
            )


# =========================================================================
# 4. Cross-scope atomicity (resources + user in one commit)
# =========================================================================


@pytest.mark.asyncio
async def test_cross_scope_atomic_commit_and_restore(vfs):
    ctx = _make_ctx(account="acct_cross")
    # Two files in distinct scopes (``user`` is a real writable scope; the
    # virtual ``session``/``agent`` scopes are not directly writable).
    await vfs.write_file("viking://resources/a.md", b"R1", ctx=ctx)
    await vfs.write_file("viking://user/notes/b.py", b"S1", ctx=ctx)
    c1 = await vfs.commit(
        message="initial",
        paths=["viking://resources/a.md", "viking://user/notes/b.py"],
        ctx=ctx,
    )
    assert c1["result"] == "created"
    assert c1["changed"] == 2

    # Both files modified
    await vfs.write_file("viking://resources/a.md", b"R2", ctx=ctx)
    await vfs.write_file("viking://user/notes/b.py", b"S2", ctx=ctx)
    await vfs.commit(
        message="v2",
        paths=["viking://resources/a.md", "viking://user/notes/b.py"],
        ctx=ctx,
    )

    # Restore only the resources scope to c1; user scope must remain at v2
    await vfs.restore(
        project_dir="viking://resources",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    assert await vfs.read("viking://resources/a.md", ctx=ctx) == b"R1"
    assert await vfs.read("viking://user/notes/b.py", ctx=ctx) == b"S2"

    # Restore the user scope too -> both back to c1
    await vfs.restore(
        project_dir="viking://user/notes",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    assert await vfs.read("viking://resources/a.md", ctx=ctx) == b"R1"
    assert await vfs.read("viking://user/notes/b.py", ctx=ctx) == b"S1"


# =========================================================================
# 5. Derived files (.abstract.md etc.) versioned with source
# =========================================================================


@pytest.mark.asyncio
async def test_derived_files_versioned_with_source(vfs):
    ctx = _make_ctx(account="acct_derived")
    await vfs.write_file("viking://resources/x.md", b"x-body", ctx=ctx)
    await vfs.write_file("viking://resources/x.md.abstract.md", b"abstract-v1", ctx=ctx)
    c1 = await vfs.commit(message="v1", ctx=ctx)
    assert c1["result"] == "created"
    assert c1["changed"] == 2

    # show finds both
    assert (
        await vfs.show("main", path="viking://resources/x.md.abstract.md", ctx=ctx)
        == b"abstract-v1"
    )

    # Update derived file
    await vfs.write_file("viking://resources/x.md.abstract.md", b"abstract-v2", ctx=ctx)
    await vfs.commit(message="v2", paths=["viking://resources/x.md.abstract.md"], ctx=ctx)

    # Restore to c1 -> derived file reverts too
    await vfs.restore(
        project_dir="viking://resources",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    body = await vfs.read("viking://resources/x.md.abstract.md", ctx=ctx)
    assert body == b"abstract-v1"


# =========================================================================
# 6. Account isolation
# =========================================================================


@pytest.mark.asyncio
async def test_account_isolation_show_misses_other_account(vfs):
    ctx_a = _make_ctx(account="acct_iso_a")
    ctx_b = _make_ctx(account="acct_iso_b")
    await vfs.write_file("viking://resources/a.md", b"a", ctx=ctx_a)
    await vfs.commit(message="m", paths=["viking://resources/a.md"], ctx=ctx_a)

    with pytest.raises(AGFSNotFoundError):
        await vfs.show("main", ctx=ctx_b)


# =========================================================================
# 7. Double-encryption end-to-end (the §3.1 invariant)
# =========================================================================


@pytest.fixture
def encryptor(workspace):
    from openviking.crypto.encryptor import FileEncryptor
    from openviking.crypto.providers import LocalFileProvider

    key_file = workspace / "master.key"
    key_file.write_text(secrets.token_bytes(32).hex())
    os.chmod(key_file, 0o600)
    provider = LocalFileProvider(key_file=str(key_file))
    return FileEncryptor(provider)


@pytest.fixture
def vfs_encrypted(workspace, encryptor):
    from openviking.storage.transaction import init_lock_manager, reset_lock_manager

    cfg, fs_root = _write_workspace(workspace)
    client = _build_client(cfg, fs_root)
    init_lock_manager(client)
    try:
        yield VikingFS(agfs=client, encryptor=encryptor)
    finally:
        reset_lock_manager()


@pytest.mark.asyncio
async def test_double_encryption_restore_preserves_plaintext(vfs_encrypted):
    """Write plaintext via encrypted VikingFS, commit (ciphertext stored in
    git), modify, restore. After restore, VikingFS.read MUST return the
    original plaintext — proving the Rust restore path bypasses the
    VikingFS encryption layer (writes ciphertext back through MountableFS,
    which then decrypts correctly on read).
    """
    ctx = _make_ctx(account="acct_enc")
    plaintext_v1 = b"top-secret-v1"
    plaintext_v2 = b"top-secret-v2"

    await vfs_encrypted.write_file("viking://resources/secret.md", plaintext_v1, ctx=ctx)
    c1 = await vfs_encrypted.commit(
        message="v1",
        paths=["viking://resources/secret.md"],
        ctx=ctx,
    )
    assert c1["result"] == "created"

    # Modify
    await vfs_encrypted.write_file("viking://resources/secret.md", plaintext_v2, ctx=ctx)
    await vfs_encrypted.commit(
        message="v2",
        paths=["viking://resources/secret.md"],
        ctx=ctx,
    )
    assert await vfs_encrypted.read("viking://resources/secret.md", ctx=ctx) == plaintext_v2

    # Restore
    result = await vfs_encrypted.restore(
        project_dir="viking://resources",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    assert result["result"] == "applied"
    assert "resources/secret.md" in result["written_paths"]

    # The critical assertion: read returns original plaintext, not garbled
    # double-encrypted bytes.
    restored = await vfs_encrypted.read("viking://resources/secret.md", ctx=ctx)
    assert restored == plaintext_v1


@pytest.mark.asyncio
async def test_encrypted_mv_file_reuses_outer_lock_handle(vfs_encrypted):
    ctx = _make_ctx(account="acct_enc")
    src_uri = "viking://resources/src.md"
    dst_uri = "viking://resources/dst.md"
    plaintext = b"top-secret-mv"

    await vfs_encrypted.write_file(src_uri, plaintext, ctx=ctx)

    await vfs_encrypted.mv(src_uri, dst_uri, ctx=ctx)

    with pytest.raises(NotFoundError):
        await vfs_encrypted.read(src_uri, ctx=ctx)

    moved = await vfs_encrypted.read(dst_uri, ctx=ctx)
    assert moved == plaintext


# =========================================================================
# 8. Feature disabled
# =========================================================================


@pytest.mark.asyncio
async def test_feature_disabled_raises_not_supported(vfs_disabled):
    ctx = _make_ctx()
    with pytest.raises(AGFSNotSupportedError):
        await vfs_disabled.commit(message="m", paths=["viking://resources/a.md"], ctx=ctx)
    with pytest.raises(AGFSNotSupportedError):
        await vfs_disabled.show("main", ctx=ctx)
    with pytest.raises(AGFSNotSupportedError):
        await vfs_disabled.restore(
            project_dir="viking://resources/proj",
            source_commit="main",
            ctx=ctx,
        )


# =========================================================================
# 9. Reindex redirect for derived files
# =========================================================================


def test_classify_restore_path(vfs):
    from openviking.core.context import ContextLevel

    # Directory-level markers -> (op, dir_uri, level)
    assert vfs._classify_restore_path("resources/proj/.abstract.md", deleted=False) == (
        "reindex_marker",
        "viking://resources/proj",
        ContextLevel.ABSTRACT,
    )
    assert vfs._classify_restore_path("resources/proj/.overview.md", deleted=False) == (
        "reindex_marker",
        "viking://resources/proj",
        ContextLevel.OVERVIEW,
    )
    assert vfs._classify_restore_path("resources/proj/.abstract.md", deleted=True) == (
        "delete",
        "viking://resources/proj",
        ContextLevel.ABSTRACT,
    )
    assert vfs._classify_restore_path("resources/proj/.overview.md", deleted=True) == (
        "delete",
        "viking://resources/proj",
        ContextLevel.OVERVIEW,
    )

    # .relations.json has no vector side-effect
    assert vfs._classify_restore_path("resources/proj/.relations.json", deleted=False) is None
    assert vfs._classify_restore_path("resources/proj/.relations.json", deleted=True) is None

    # Per-file sidecars do NOT exist in production -> treated as ordinary source files
    assert vfs._classify_restore_path("resources/proj/x.md.abstract.md", deleted=False) == (
        "reindex_file",
        "viking://resources/proj/x.md.abstract.md",
        ContextLevel.DETAIL,
    )
    assert vfs._classify_restore_path("resources/proj/x.md.overview.md", deleted=True) == (
        "delete",
        "viking://resources/proj/x.md.overview.md",
        ContextLevel.DETAIL,
    )

    # Source files -> DETAIL reindex/delete
    assert vfs._classify_restore_path("resources/proj/x.md", deleted=False) == (
        "reindex_file",
        "viking://resources/proj/x.md",
        ContextLevel.DETAIL,
    )
    assert vfs._classify_restore_path("resources/proj/x.md", deleted=True) == (
        "delete",
        "viking://resources/proj/x.md",
        ContextLevel.DETAIL,
    )

    # Directory marker at the account root -> None (no parent dir to scope)
    assert vfs._classify_restore_path(".abstract.md", deleted=False) is None

    # Account-root .ovgitignore is versioned but has no vector side-effect.
    assert vfs._classify_restore_path(".ovgitignore", deleted=False) is None
    assert vfs._classify_restore_path(".ovgitignore", deleted=True) is None


class _SpyExecutor:
    """Records every scheduled vector task as a normalized tuple."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def execute(self, *, uri, mode, wait, ctx):
        self.calls.append(("reindex_file", uri))
        return {"ok": True}

    async def reindex_directory_marker(self, *, dir_uri, level, ctx):
        self.calls.append(("reindex_marker", dir_uri, int(level)))

    async def delete_uri_level(self, *, uri, level, ctx):
        self.calls.append(("delete", uri, int(level)))
        return 0


@pytest.mark.asyncio
async def test_restore_schedules_reindex_for_derived_only_change(vfs, monkeypatch):
    """When a restore only changes a directory `.abstract.md` (source file
    unchanged), exactly that directory's L0 vector must be recomputed via
    reindex_directory_marker — and nothing else (no whole-tree rebuild).
    """
    spy = _SpyExecutor()

    import openviking.service.reindex_executor as reindex_mod

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: spy)

    ctx = _make_ctx(account="acct_derived_only")
    await vfs.write_file("viking://resources/proj/x.md", b"body", ctx=ctx)
    await vfs.write_file("viking://resources/proj/.abstract.md", b"abs-v1", ctx=ctx)
    c1 = await vfs.commit(message="v1", ctx=ctx)
    assert c1["result"] == "created"

    # Modify ONLY the directory marker; source file untouched
    await vfs.write_file("viking://resources/proj/.abstract.md", b"abs-v2", ctx=ctx)
    c2 = await vfs.commit(
        message="v2",
        paths=["viking://resources/proj/.abstract.md"],
        ctx=ctx,
    )
    assert c2["result"] == "created"
    assert c2["changed"] == 1

    result = await vfs.restore(
        project_dir="viking://resources/proj",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    assert result["result"] == "applied"
    assert "resources/proj/.abstract.md" in result["written_paths"]

    # Let the fire-and-forget tasks run
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert spy.calls == [("reindex_marker", "viking://resources/proj", 0)]


@pytest.mark.asyncio
async def test_restore_schedules_marker_and_files_independently(vfs, monkeypatch):
    """Ancestor subsumption is gone: a changed directory marker recomputes the
    directory's L0/L1, while each changed source file independently reindexes
    its own DETAIL vector — neither subsumes the other.
    """
    spy = _SpyExecutor()

    import openviking.service.reindex_executor as reindex_mod

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: spy)

    ctx = _make_ctx(account="acct_dedup")
    await vfs.write_file("viking://resources/proj/x.md", b"v1", ctx=ctx)
    await vfs.write_file("viking://resources/proj/y.md", b"yv1", ctx=ctx)
    await vfs.write_file("viking://resources/proj/.abstract.md", b"a-v1", ctx=ctx)
    c1 = await vfs.commit(message="v1", ctx=ctx)

    await vfs.write_file("viking://resources/proj/x.md", b"v2", ctx=ctx)
    await vfs.write_file("viking://resources/proj/y.md", b"yv2", ctx=ctx)
    await vfs.write_file("viking://resources/proj/.abstract.md", b"a-v2", ctx=ctx)
    await vfs.commit(message="v2", ctx=ctx)

    await vfs.restore(
        project_dir="viking://resources/proj",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Directory marker recompute + each source file's DETAIL, all independent.
    assert sorted(spy.calls) == sorted(
        [
            ("reindex_marker", "viking://resources/proj", 0),
            ("reindex_file", "viking://resources/proj/x.md"),
            ("reindex_file", "viking://resources/proj/y.md"),
        ]
    )


@pytest.mark.asyncio
async def test_restore_schedules_siblings_independently(vfs, monkeypatch):
    """Source files in sibling directories are each scheduled independently;
    a directory marker change only affects its own directory.
    """
    spy = _SpyExecutor()

    import openviking.service.reindex_executor as reindex_mod

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: spy)

    ctx = _make_ctx(account="acct_subsume_sibling")
    # proj_a: source file + directory marker
    await vfs.write_file("viking://resources/proj_a/x.md", b"v1", ctx=ctx)
    await vfs.write_file("viking://resources/proj_a/.abstract.md", b"a-v1", ctx=ctx)
    # proj_b: source file only — sibling directory
    await vfs.write_file("viking://resources/proj_b/y.md", b"v1", ctx=ctx)
    c1 = await vfs.commit(message="v1", ctx=ctx)

    await vfs.write_file("viking://resources/proj_a/x.md", b"v2", ctx=ctx)
    await vfs.write_file("viking://resources/proj_a/.abstract.md", b"a-v2", ctx=ctx)
    await vfs.write_file("viking://resources/proj_b/y.md", b"v2", ctx=ctx)
    await vfs.commit(message="v2", ctx=ctx)

    # Restore the whole resources scope so proj_a + proj_b both revert
    await vfs.restore(
        project_dir="viking://resources",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert sorted(spy.calls) == sorted(
        [
            ("reindex_marker", "viking://resources/proj_a", 0),
            ("reindex_file", "viking://resources/proj_a/x.md"),
            ("reindex_file", "viking://resources/proj_b/y.md"),
        ]
    )


@pytest.mark.asyncio
async def test_restore_deletes_marker_and_source_vectors(vfs, monkeypatch):
    """Bug 1 regression: restoring to a revision that predates a whole
    directory must delete BOTH the directory's L0/L1 marker vectors and the
    deleted source file's DETAIL vector — no orphaned vectors left behind.
    """
    spy = _SpyExecutor()

    import openviking.service.reindex_executor as reindex_mod

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: spy)

    ctx = _make_ctx(account="acct_del_marker")
    await vfs.write_file("viking://resources/keep/k.md", b"keep", ctx=ctx)
    c1 = await vfs.commit(message="v1", ctx=ctx)

    # v2 adds a whole new directory with a source file + directory markers.
    await vfs.write_file("viking://resources/gone/g.md", b"gone", ctx=ctx)
    await vfs.write_file("viking://resources/gone/.abstract.md", b"abs", ctx=ctx)
    await vfs.write_file("viking://resources/gone/.overview.md", b"ovr", ctx=ctx)
    await vfs.commit(message="v2", ctx=ctx)

    # Restore back to v1: everything under gone/ must be removed.
    result = await vfs.restore(
        project_dir="viking://resources",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    assert result["result"] == "applied"
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert ("delete", "viking://resources/gone", 0) in spy.calls
    assert ("delete", "viking://resources/gone", 1) in spy.calls
    assert ("delete", "viking://resources/gone/g.md", 2) in spy.calls
    # No whole-tree reindex of the deleted dir.
    assert all(c[0] != "reindex_marker" or c[1] != "viking://resources/gone" for c in spy.calls)


@pytest.mark.asyncio
async def test_restore_relations_json_has_no_vector_side_effect(vfs, monkeypatch):
    """A restore that only touches `.relations.json` must schedule no vector
    reindex/delete tasks at all.
    """
    spy = _SpyExecutor()

    import openviking.service.reindex_executor as reindex_mod

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: spy)

    ctx = _make_ctx(account="acct_relations")
    await vfs.write_file("viking://resources/proj/.relations.json", b'{"v":1}', ctx=ctx)
    c1 = await vfs.commit(message="v1", ctx=ctx)

    await vfs.write_file("viking://resources/proj/.relations.json", b'{"v":2}', ctx=ctx)
    c2 = await vfs.commit(
        message="v2",
        paths=["viking://resources/proj/.relations.json"],
        ctx=ctx,
    )
    assert c2["result"] == "created"

    result = await vfs.restore(
        project_dir="viking://resources/proj",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    assert result["result"] == "applied"
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert spy.calls == []
    # No vector side-effect -> no tracked task.
    assert "task_id" not in result


@pytest.mark.asyncio
async def test_restore_returns_pollable_task_id(vfs, monkeypatch):
    """An applied restore with vector side-effects returns a ``task_id`` that
    can be polled via the TaskTracker and reaches ``completed``.
    """
    spy = _SpyExecutor()

    import openviking.service.reindex_executor as reindex_mod

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: spy)

    from openviking.service.task_tracker import (
        TaskTracker,
        set_task_tracker,
    )

    class _MemTaskStore:
        async def create(self, task):
            return None

        async def update(self, task):
            return None

        async def get(self, task_id, *, account_id=None, user_id=None):
            return None

        async def list(self, account_id, *, user_id=None):
            return []

        async def delete(self, task_id, *, account_id, user_id=None):
            return None

    set_task_tracker(TaskTracker(store=_MemTaskStore()))
    try:
        ctx = _make_ctx(account="acct_taskid")
        await vfs.write_file("viking://resources/proj/x.md", b"v1", ctx=ctx)
        c1 = await vfs.commit(message="v1", ctx=ctx)
        await vfs.write_file("viking://resources/proj/x.md", b"v2", ctx=ctx)
        await vfs.commit(message="v2", ctx=ctx)

        result = await vfs.restore(
            project_dir="viking://resources/proj",
            source_commit=c1["commit_oid"],
            ctx=ctx,
        )
        assert result["result"] == "applied"
        task_id = result.get("task_id")
        assert task_id

        # Let the tracked background worker run to completion.
        for _ in range(5):
            await asyncio.sleep(0)

        from openviking.service.task_tracker import get_task_tracker

        tracker = get_task_tracker()
        task = await tracker.get(task_id, account_id=ctx.account_id, user_id=ctx.user.user_id)
        assert task is not None
        assert task.task_type == "snapshot_restore_reindex"
        assert task.status.value == "completed"
        assert ("reindex_file", "viking://resources/proj/x.md") in spy.calls
    finally:
        set_task_tracker(None)


# =========================================================================
# 7. Restore concurrency / locking
# =========================================================================


@pytest.mark.asyncio
async def test_restore_concurrent_same_dir_is_rejected(vfs, monkeypatch):
    """While one restore holds the project_dir tree lock during writeback, a
    second restore on the same subtree fails fast with ResourceBusyError
    (manager default timeout is non-blocking).
    """
    from openviking.storage.errors import ResourceBusyError

    spy = _SpyExecutor()
    import openviking.service.reindex_executor as reindex_mod

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: spy)

    ctx = _make_ctx(account="acct_lock_same")
    await vfs.write_file("viking://resources/proj/a.md", b"v1", ctx=ctx)
    c1 = await vfs.commit(message="v1", paths=["viking://resources/proj/a.md"], ctx=ctx)
    await vfs.write_file("viking://resources/proj/a.md", b"v2", ctx=ctx)
    await vfs.commit(message="v2", paths=["viking://resources/proj/a.md"], ctx=ctx)

    # Gate the first restore *inside* the writeback so it keeps holding the
    # tree lock while the second restore attempts to acquire it.
    orig_run = vfs._async_agfs.run
    holding_lock = asyncio.Event()
    release = asyncio.Event()

    async def gated_run(method_name, *args, **kwargs):
        if method_name == "git_restore" and not kwargs.get("dry_run"):
            holding_lock.set()
            await release.wait()
        return await orig_run(method_name, *args, **kwargs)

    monkeypatch.setattr(vfs._async_agfs, "run", gated_run)

    first = asyncio.create_task(
        vfs.restore(
            project_dir="viking://resources/proj",
            source_commit=c1["commit_oid"],
            ctx=ctx,
        )
    )
    await asyncio.wait_for(holding_lock.wait(), timeout=5)

    # Second restore on the same subtree must be rejected immediately.
    with pytest.raises(ResourceBusyError):
        await vfs.restore(
            project_dir="viking://resources/proj",
            source_commit=c1["commit_oid"],
            ctx=ctx,
        )

    # Release the first restore and let it complete normally.
    release.set()
    result = await asyncio.wait_for(first, timeout=5)
    assert result["result"] == "applied"

    # Lock released: a follow-up restore on the same subtree now succeeds.
    again = await vfs.restore(
        project_dir="viking://resources/proj",
        source_commit=c1["commit_oid"],
        ctx=ctx,
    )
    assert again["result"] in ("applied", "noop")


@pytest.mark.asyncio
async def test_restore_concurrent_sibling_dirs_do_not_block(vfs, monkeypatch):
    """Restores on sibling subtrees hold disjoint tree locks and run
    concurrently — neither blocks the other.
    """
    spy = _SpyExecutor()
    import openviking.service.reindex_executor as reindex_mod

    monkeypatch.setattr(reindex_mod, "get_reindex_executor", lambda: spy)

    ctx = _make_ctx(account="acct_lock_sibling")
    await vfs.write_file("viking://resources/proj_a/x.md", b"v1", ctx=ctx)
    await vfs.write_file("viking://resources/proj_b/y.md", b"v1", ctx=ctx)
    c1 = await vfs.commit(message="v1", ctx=ctx)
    await vfs.write_file("viking://resources/proj_a/x.md", b"v2", ctx=ctx)
    await vfs.write_file("viking://resources/proj_b/y.md", b"v2", ctx=ctx)
    await vfs.commit(message="v2", ctx=ctx)

    # Block both writebacks until both have entered, proving they hold their
    # (distinct) tree locks simultaneously. If the locks conflicted, the second
    # restore would raise ResourceBusyError before reaching git_restore and
    # ``both_in`` would never fire.
    orig_run = vfs._async_agfs.run
    both_in = asyncio.Event()
    release_a = asyncio.Event()
    release_b = asyncio.Event()
    entered = 0

    async def gated_run(method_name, *args, **kwargs):
        nonlocal entered
        if method_name == "git_restore" and not kwargs.get("dry_run"):
            entered += 1
            if entered == 2:
                both_in.set()
            # proj_a is released first; proj_b waits on its own gate.
            await (
                release_a if kwargs.get("project_dir", "").endswith("proj_a") else release_b
            ).wait()
        return await orig_run(method_name, *args, **kwargs)

    monkeypatch.setattr(vfs._async_agfs, "run", gated_run)

    task_a = asyncio.create_task(
        vfs.restore(
            project_dir="viking://resources/proj_a",
            source_commit=c1["commit_oid"],
            ctx=ctx,
        )
    )
    task_b = asyncio.create_task(
        vfs.restore(
            project_dir="viking://resources/proj_b",
            source_commit=c1["commit_oid"],
            ctx=ctx,
        )
    )
    # Both reaching the writeback concurrently proves the tree locks are disjoint.
    await asyncio.wait_for(both_in.wait(), timeout=5)

    # Drain sequentially: both share branch ``main``, so the final ref CAS would
    # otherwise conflict — that guard is independent of the directory lock.
    release_a.set()
    res_a = await asyncio.wait_for(task_a, timeout=5)
    release_b.set()
    res_b = await asyncio.wait_for(task_b, timeout=5)
    assert res_a["result"] == "applied"
    assert res_b["result"] == "applied"


@pytest.mark.asyncio
async def test_vikingfs_gitignore_management_methods(vfs):
    ctx = _make_ctx(account="acct_gitignore_methods")

    assert await vfs.get_gitignore(ctx=ctx) == ""

    await vfs.set_gitignore("*.log\n", ctx=ctx)
    assert await vfs.get_gitignore(ctx=ctx) == "*.log\n"

    result = await vfs.commit(message="track ignore", ctx=ctx)
    assert result["result"] == "created"
    if "ignored" in result:
        assert result["ignored"] == 0

    # Use get_gitignore to verify it's tracked rather than show()
    assert await vfs.get_gitignore(ctx=ctx) == "*.log\n"

    await vfs.delete_gitignore(ctx=ctx)
    assert await vfs.get_gitignore(ctx=ctx) == ""
    await vfs.delete_gitignore(ctx=ctx)
    assert await vfs.get_gitignore(ctx=ctx) == ""


@pytest.mark.asyncio
async def test_vikingfs_commit_respects_account_gitignore(vfs):
    ctx = _make_ctx(account="acct_vfs_ignore")
    await vfs.set_gitignore("*.log\n", ctx=ctx)
    await vfs.write_file("viking://resources/keep.md", b"keep", ctx=ctx)
    await vfs.write_file("viking://resources/skip.log", b"skip", ctx=ctx)

    result = await vfs.commit(message="ignore logs", ctx=ctx)

    assert result["result"] == "created"
    if "ignored" in result:
        assert result["ignored"] == 1
    assert await vfs.show("main", path="viking://resources/keep.md", ctx=ctx) == b"keep"

    # Use get_gitignore instead of show() for account-root .ovgitignore
    assert await vfs.get_gitignore(ctx=ctx) == "*.log\n"

    with pytest.raises(AGFSNotFoundError):
        await vfs.show("main", path="viking://resources/skip.log", ctx=ctx)


@pytest.mark.asyncio
async def test_vikingfs_set_gitignore_rejects_oversized_content(vfs):
    ctx = _make_ctx(account="acct_gitignore_too_large")

    oversized = "a" * (vfs._OVGITIGNORE_MAX_BYTES + 1)
    with pytest.raises(AGFSInvalidOperationError) as excinfo:
        await vfs.set_gitignore(oversized, ctx=ctx)
    assert "too large" in str(excinfo.value).lower()

    # The oversized file was never persisted (write was rejected before any
    # AGFS call): get_gitignore reports the file absent, and committing a
    # freshly-created file works without an ignore-poison failure.
    assert await vfs.get_gitignore(ctx=ctx) == ""
    await vfs.write_file("viking://resources/keep.md", b"keep", ctx=ctx)
    result = await vfs.commit(message="no ignore", ctx=ctx)
    assert result["ignored"] == 0


@pytest.mark.asyncio
async def test_vikingfs_get_gitignore_maps_non_utf8_to_invalid_operation(vfs):
    ctx = _make_ctx(account="acct_gitignore_bad_utf8")

    # Seed a valid file (also creates the account dir), then overwrite it with
    # non-UTF-8 bytes through the raw AGFS path, bypassing set_gitignore.
    await vfs.set_gitignore("*.log\n", ctx=ctx)
    path = vfs._gitignore_agfs_path(ctx)
    await vfs._async_agfs.write(path, b"*.log\n\xff\xfe\n")

    with pytest.raises(AGFSInvalidOperationError) as excinfo:
        await vfs.get_gitignore(ctx=ctx)
    assert "utf-8" in str(excinfo.value).lower()
