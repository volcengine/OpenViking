"""End-to-end tests for the git_commit/git_restore/git_show PyO3 bindings.

These tests exercise the binding through ragfs_python.RAGFSBindingClient
directly so they don't require the higher-level VikingFS layer.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

# ---------------- fixtures ----------------


@pytest.fixture
def git_workspace():
    """Create a temp workspace containing a localfs root and a [git] config TOML.

    Yields (config_path, localfs_root, git_root) and removes the dir on exit.
    """
    root = Path(tempfile.mkdtemp(prefix="ov-git-binding-"))
    localfs_root = root / "fs"
    git_root = root / "git"
    localfs_root.mkdir()
    git_root.mkdir()

    config_path = root / "ragfs.toml"
    config_path.write_text(
        f"""
[git]
enabled = true
backend = "local"
default_branch = "main"
author_name = "test-bot"
author_email = "test@example.com"

[git.local]
base_dir = "{git_root}"

[git.snapshot.log]
provider = "none"
"""
    )

    yield config_path, localfs_root, git_root

    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def git_disabled_workspace():
    """A workspace whose [git] section has enabled = false."""
    root = Path(tempfile.mkdtemp(prefix="ov-git-disabled-"))
    config_path = root / "ragfs.toml"
    config_path.write_text(
        """
[git]
enabled = false
"""
    )
    yield config_path
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def client(git_workspace, ragfs_client_type):
    config_path, localfs_root, _ = git_workspace
    client = ragfs_client_type(git_config_path=str(config_path))
    try:
        client.mount("localfs", "/local", {"local_dir": str(localfs_root)})
        yield client
    finally:
        client.close()


# ---------------- helper: write a file into account tree ----------------


def _write(client, account: str, rel_path: str, body: bytes) -> str:
    """Write `body` to /local/<account>/<rel_path> via the binding."""
    path = f"/local/{account}/{rel_path}"
    client.ensure_parent_dirs(path)
    client.write(path, body)
    return path


# ---------------- tests ----------------


def test_health_reports_git_enabled(client):
    h = client.health()
    assert h["git_enabled"] == "true"
    assert h.get("git_backend") == "local"


def test_git_path_is_file_checks_exact_tree_entry_at_commit(client):
    account = "acct1"
    path = "resources/proj/a.md"
    _write(client, account, path, b"tracked file")
    commit = client.git_commit(
        account=account,
        branch="main",
        message="initial",
        author_name="alice",
        author_email="a@e.com",
        paths=[path],
    )
    commit_oid = commit["commit_oid"]
    assert client.git_path_is_file(account=account, commit_oid=commit_oid, path=path)
    assert not client.git_path_is_file(
        account=account, commit_oid=commit_oid, path="resources/proj"
    )
    assert not client.git_path_is_file(
        account=account, commit_oid=commit_oid, path="resources/absent"
    )
    with pytest.raises(ValueError, match="full commit OID"):
        client.git_path_is_file(account=account, commit_oid="main", path=path)


def test_snapshot_show_preview_and_restore_roundtrip(client):
    account = "acct1"
    _write(client, account, "resources/proj/a.md", b"v1-content")
    _write(client, account, "resources/proj/b.md", b"retained in v1")

    v1 = client.git_commit(
        account=account,
        branch="main",
        message="v1",
        author_name="a",
        author_email="a@e",
        paths=["resources/proj"],
    )
    assert v1["result"] == "created"
    assert v1["changed"] == 2
    v1_oid = v1["commit_oid"]
    meta = client.git_show(account=account, target_ref="main")
    assert meta["oid"] == v1_oid
    assert meta["parents"] == []
    assert meta["message"].startswith("v1")
    assert meta["author"]["name"] == "a"
    blob = client.git_show(account=account, target_ref="main", path="resources/proj/a.md")
    assert (blob["bytes"], blob["size"]) == (b"v1-content", len(b"v1-content"))

    _write(client, account, "resources/proj/a.md", b"v2-content")
    client.rm(f"/local/{account}/resources/proj/b.md")
    _write(client, account, "resources/proj/c.md", b"added in v2")
    _write(client, account, "resources/other/a.md", b"outside project")
    v2 = client.git_commit(
        account=account,
        branch="main",
        message="v2",
        author_name="a",
        author_email="a@e",
        paths=["resources/proj", "resources/other"],
    )
    v2_oid = v2["commit_oid"]

    assert client.git_diff_paths(
        account=account, base=None, head=v1_oid, paths=["resources/proj"]
    ) == [("resources/proj/a.md", "added"), ("resources/proj/b.md", "added")]
    assert client.git_diff_paths(
        account=account, base=v1_oid, head=v2_oid, paths=["resources/proj"]
    ) == [
        ("resources/proj/a.md", "modified"),
        ("resources/proj/b.md", "deleted"),
        ("resources/proj/c.md", "added"),
    ]

    preview = client.git_restore(
        account=account,
        branch="main",
        project_dir="resources/proj",
        source_commit=v1_oid,
        author_name="a",
        author_email="a@e",
        dry_run=True,
    )
    assert preview["result"] == "dry_run"
    assert {item["path"] for item in preview["diff"]["to_write"]} == {"a.md", "b.md"}
    assert client.read(f"/local/{account}/resources/proj/a.md") == b"v2-content"
    assert client.read(f"/local/{account}/resources/proj/c.md") == b"added in v2"
    assert client.git_show(account=account, target_ref="main")["oid"] == v2_oid

    restored = client.git_restore(
        account=account,
        branch="main",
        project_dir="resources/proj",
        source_commit=v1_oid,
        author_name="a",
        author_email="a@e",
    )
    assert restored["result"] == "applied"
    assert restored["source_commit"] == v1_oid
    assert restored["parent_commit"] == v2_oid
    assert restored["new_commit_oid"] != v2_oid
    assert restored["written"] >= 1

    # VFS file content reverted
    content = client.read(f"/local/{account}/resources/proj/a.md")
    assert content == b"v1-content"
    assert client.read(f"/local/{account}/resources/proj/b.md") == b"retained in v1"
    assert client.read(f"/local/{account}/resources/other/a.md") == b"outside project"

    # Branch now points at the new commit
    head = client.git_show(account=account, target_ref="main")
    assert head["oid"] == restored["new_commit_oid"]
    assert head["parents"] == [v2_oid]
    assert (
        client.git_diff_paths(account=account, base=v1_oid, head="main", paths=["resources/proj"])
        == []
    )


def test_account_isolation(client):
    """A commit under account A is invisible to account B."""
    _write(client, "acct_a", "resources/a.md", b"x")
    client.git_commit(
        account="acct_a", branch="main", message="m",
        author_name="n", author_email="e",
        paths=["resources/a.md"],
    )

    from openviking.pyagfs import AGFSNotFoundError
    with pytest.raises(AGFSNotFoundError):
        client.git_show(account="acct_b", target_ref="main")


def test_feature_disabled_raises(git_disabled_workspace, ragfs_client_type):
    from openviking.pyagfs import AGFSNotSupportedError

    c = ragfs_client_type(git_config_path=str(git_disabled_workspace))
    try:
        with pytest.raises(AGFSNotSupportedError):
            c.git_commit(
                account="a",
                branch="main",
                message="m",
                author_name="n",
                author_email="e",
            )
    finally:
        c.close()


def test_invalid_backend_at_construct_time(tmp_path, ragfs_client_type):
    cfg = tmp_path / "bad.toml"
    cfg.write_text(
        """
[git]
enabled = true
backend = "bogus"
"""
    )
    with pytest.raises(Exception) as excinfo:
        ragfs_client_type(git_config_path=str(cfg))
    assert "unsupported git backend" in str(excinfo.value).lower()


def test_cas_conflict_surface(client):
    """Concurrent commits must preserve every acknowledged change on the branch."""
    import threading

    from openviking.pyagfs import GitConcurrentCommitError

    account = "acct_cas"
    _write(client, account, "resources/seed.md", b"seed")
    client.git_commit(
        account=account, branch="main", message="seed",
        author_name="n", author_email="e",
        paths=["resources/seed.md"],
    )

    # Prepare two divergent changes
    _write(client, account, "resources/a.md", b"AAA")
    _write(client, account, "resources/b.md", b"BBB")

    errors: list[BaseException] = []
    committed_paths: list[str] = []
    barrier = threading.Barrier(2)

    def do_commit(path: str):
        try:
            barrier.wait(timeout=5)
            client.git_commit(
                account=account,
                branch="main",
                message=f"commit {path}",
                author_name="n",
                author_email="e",
                paths=[path],
            )
            committed_paths.append(path)
        except BaseException as e:
            errors.append(e)

    t1 = threading.Thread(target=do_commit, args=("resources/a.md",))
    t2 = threading.Thread(target=do_commit, args=("resources/b.md",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert committed_paths and len(committed_paths) + len(errors) == 2
    assert all(isinstance(error, GitConcurrentCommitError) for error in errors)
    expected = {"resources/seed.md": b"seed", "resources/a.md": b"AAA", "resources/b.md": b"BBB"}
    for path in ["resources/seed.md", *committed_paths]:
        shown = client.git_show(account=account, target_ref="main", path=path)
        assert shown["bytes"] == expected[path]


def test_commit_respects_account_ovgitignore(client):
    account = "acct_ignore_binding"
    _write(client, account, ".ovgitignore", b"*.log\n")
    _write(client, account, "resources/keep.md", b"keep")
    _write(client, account, "resources/skip.log", b"skip")

    resp = client.git_commit(
        account=account,
        branch="main",
        message="ignore",
        author_name="tester",
        author_email="tester@example.com",
    )

    assert resp["result"] == "created"
    assert resp["ignored"] == 1
    assert client.git_show(
        account=account,
        target_ref="main",
        path="resources/keep.md",
    )["bytes"] == b"keep"
    assert client.git_show(
        account=account,
        target_ref="main",
        path=".ovgitignore",
    )["bytes"] == b"*.log\n"

    from openviking.pyagfs import AGFSNotFoundError
    with pytest.raises(AGFSNotFoundError):
        client.git_show(
            account=account,
            target_ref="main",
            path="resources/skip.log",
        )


def test_commit_invalid_ovgitignore_maps_to_invalid_operation(client):
    account = "acct_bad_ignore_binding"
    _write(client, account, ".ovgitignore", b"!keep.log\n")

    from openviking.pyagfs import AGFSInvalidOperationError
    with pytest.raises(AGFSInvalidOperationError) as excinfo:
        client.git_commit(
            account=account,
            branch="main",
            message="bad ignore",
            author_name="tester",
            author_email="tester@example.com",
        )

    assert "invalid ignore file" in str(excinfo.value).lower()
    assert "negation" in str(excinfo.value).lower()


def test_restore_does_not_apply_current_ovgitignore(client):
    account = "acct_restore_ignore"
    _write(client, account, "resources/proj/a.log", b"v1")
    v1 = client.git_commit(
        account=account,
        branch="main",
        message="v1",
        author_name="tester",
        author_email="tester@example.com",
    )

    _write(client, account, ".ovgitignore", b"*.log\n")
    client.write(f"/local/{account}/resources/proj/a.log", b"v2")
    v2 = client.git_commit(
        account=account,
        branch="main",
        message="ignore logs",
        author_name="tester",
        author_email="tester@example.com",
    )
    assert v2["ignored"] >= 1

    restored = client.git_restore(
        account=account,
        branch="main",
        project_dir="resources/proj",
        source_commit=v1["commit_oid"],
        author_name="tester",
        author_email="tester@example.com",
    )

    assert restored["result"] == "applied"
    assert client.read(f"/local/{account}/resources/proj/a.log") == b"v1"
    assert client.git_show(
        account=account,
        target_ref="main",
        path="resources/proj/a.log",
    )["bytes"] == b"v1"
