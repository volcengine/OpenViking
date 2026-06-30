"""End-to-end tests for the git_commit/git_restore/git_show PyO3 bindings.

These tests exercise the binding through ragfs_python.RAGFSBindingClient
directly so they don't require the higher-level VikingFS layer.
"""

import shutil
import tempfile
from pathlib import Path

import pytest


# Skip the whole module if the native extension is not built locally.
ragfs_python = pytest.importorskip("ragfs_python")


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
def client(git_workspace):
    config_path, localfs_root, _ = git_workspace
    c = ragfs_python.RAGFSBindingClient(git_config_path=str(config_path))
    # Mount localfs at /local so we can write files into the account tree.
    c.mount("localfs", "/local", {"local_dir": str(localfs_root)})
    return c


# ---------------- helper: write a file into account tree ----------------


def _write(client, account: str, rel_path: str, body: bytes) -> str:
    """Write `body` to /local/<account>/<rel_path> via the binding."""
    path = f"/local/{account}/{rel_path}"
    client.ensure_parent_dirs(path)
    client.write(path, body)
    return path


# ---------------- tests ----------------


def test_git_concurrent_commit_error_class_exists():
    from openviking.pyagfs import GitConcurrentCommitError
    from openviking.pyagfs.exceptions import AGFSClientError
    assert issubclass(GitConcurrentCommitError, AGFSClientError)


def test_health_reports_git_enabled(client):
    h = client.health()
    assert h["git_enabled"] == "true"
    assert h.get("git_backend") == "local"


def test_commit_then_show_roundtrip(client):
    """Write a file, commit it, then show it back and verify bytes match."""
    account = "acct1"
    _write(client, account, "resources/a.md", b"hello world")

    resp = client.git_commit(
        account=account,
        branch="main",
        message="initial",
        author_name="alice",
        author_email="a@e.com",
        paths=["resources/a.md"],
    )
    assert resp["result"] == "created"
    assert resp["changed"] == 1
    commit_oid = resp["commit_oid"]
    assert len(commit_oid) == 40

    shown = client.git_show(
        account=account,
        target_ref="main",
        path="resources/a.md",
    )
    assert shown["bytes"] == b"hello world"
    assert shown["size"] == 11


def test_commit_then_show_commit_metadata(client):
    account = "acct1"
    _write(client, account, "resources/a.md", b"x")
    resp = client.git_commit(
        account=account,
        branch="main",
        message="m1",
        author_name="alice",
        author_email="a@e.com",
        paths=["resources/a.md"],
    )
    meta = client.git_show(account=account, target_ref="main")
    assert meta["message"].startswith("m1")
    assert meta["oid"] == resp["commit_oid"]
    assert meta["parents"] == []
    assert meta["author"]["name"] == "alice"


def test_restore_roundtrip(client):
    """Commit v1 → modify → commit v2 → restore v1 → file reverts; HEAD moves to v3."""
    account = "acct1"
    _write(client, account, "resources/proj/a.md", b"v1-content")

    v1 = client.git_commit(
        account=account, branch="main", message="v1",
        author_name="a", author_email="a@e",
        paths=["resources/proj/a.md"],
    )
    v1_oid = v1["commit_oid"]

    _write(client, account, "resources/proj/a.md", b"v2-content")
    v2 = client.git_commit(
        account=account, branch="main", message="v2",
        author_name="a", author_email="a@e",
        paths=["resources/proj/a.md"],
    )
    v2_oid = v2["commit_oid"]

    restored = client.git_restore(
        account=account, branch="main",
        project_dir="resources/proj",
        source_commit=v1_oid,
        author_name="a", author_email="a@e",
    )
    assert restored["result"] == "applied"
    assert restored["source_commit"] == v1_oid
    assert restored["parent_commit"] == v2_oid
    assert restored["new_commit_oid"] != v2_oid
    assert restored["written"] >= 1

    # VFS file content reverted
    content = client.read(f"/local/{account}/resources/proj/a.md")
    assert content == b"v1-content"

    # Branch now points at the new commit
    head = client.git_show(account=account, target_ref="main")
    assert head["oid"] == restored["new_commit_oid"]
    assert head["parents"] == [v2_oid]


def test_restore_dry_run_does_not_mutate(client):
    account = "acct1"
    _write(client, account, "resources/proj/a.md", b"v1")
    v1 = client.git_commit(
        account=account, branch="main", message="v1",
        author_name="a", author_email="a@e",
        paths=["resources/proj/a.md"],
    )

    _write(client, account, "resources/proj/a.md", b"v2")
    client.git_commit(
        account=account, branch="main", message="v2",
        author_name="a", author_email="a@e",
        paths=["resources/proj/a.md"],
    )

    res = client.git_restore(
        account=account, branch="main",
        project_dir="resources/proj",
        source_commit=v1["commit_oid"],
        author_name="a", author_email="a@e",
        dry_run=True,
    )
    assert res["result"] == "dry_run"
    assert "diff" in res
    assert any(item["path"] == "a.md" for item in res["diff"]["to_write"])

    # VFS still holds v2 — dry_run did not write
    assert client.read(f"/local/{account}/resources/proj/a.md") == b"v2"


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


def test_feature_disabled_raises(git_disabled_workspace):
    from openviking.pyagfs import AGFSNotSupportedError
    c = ragfs_python.RAGFSBindingClient(git_config_path=str(git_disabled_workspace))
    with pytest.raises(AGFSNotSupportedError):
        c.git_commit(
            account="a", branch="main", message="m",
            author_name="n", author_email="e",
        )


def test_invalid_backend_at_construct_time(tmp_path):
    cfg = tmp_path / "bad.toml"
    cfg.write_text(
        """
[git]
enabled = true
backend = "bogus"
"""
    )
    with pytest.raises(Exception) as excinfo:
        ragfs_python.RAGFSBindingClient(git_config_path=str(cfg))
    assert "unsupported git backend" in str(excinfo.value).lower()


def test_cas_conflict_surface(client):
    """Two commits trying to advance from the same parent — one should win,
    the other should raise GitConcurrentCommitError.

    We provoke this by writing two different files, then issuing two
    git_commit calls back-to-back with paths overlapping enough that both
    actually produce new tree objects.
    """
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
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def do_commit(path: str):
        try:
            barrier.wait()
            r = client.git_commit(
                account=account, branch="main", message=f"commit {path}",
                author_name="n", author_email="e",
                paths=[path],
            )
            results.append(r)
        except BaseException as e:
            errors.append(e)

    t1 = threading.Thread(target=do_commit, args=("resources/a.md",))
    t2 = threading.Thread(target=do_commit, args=("resources/b.md",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # The LocalRefStore mutex + CAS may serialize the two so well that the
    # second sees the new parent and the conflict never surfaces. In that case
    # both succeed and form a linear history — that is also correct behavior.
    # We accept either outcome but verify that NO silent data loss occurs:
    # if both succeed, the second's commit_oid != the first's; if one fails,
    # the failure must be GitConcurrentCommitError.
    if len(errors) == 1:
        assert isinstance(errors[0], GitConcurrentCommitError), errors[0]
        assert len(results) == 1
    else:
        assert errors == [], errors
        assert len(results) == 2
        assert results[0]["commit_oid"] != results[1]["commit_oid"]
