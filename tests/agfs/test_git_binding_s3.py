# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""End-to-end tests for the git_commit/git_restore/git_show PyO3 bindings on
the **S3/TOS** backend (audit plan §A4).

These mirror the local-backend flow in ``test_git_binding.py`` but wire the
binding to a real S3-compatible store (TOS / MinIO / LocalStack):

* git objects/refs go to ``[git.s3]`` (namespaced under ``{prefix}/_it/{uuid}``)
* the working tree is an ``s3fs`` mount at ``/local`` (namespaced under a
  separate ``_it_fs/{uuid}`` prefix)

so concurrent runs never collide and never touch real data.

Skip gating mirrors ``test_fs_binding_s3.py``: the whole module is skipped
unless a config file with ``git.enabled = true`` and ``git.backend = "s3"`` is
discoverable. Resolution order (first hit wins):

  1. ``OV_GIT_S3_CONF`` env var (explicit path to an ``ov.conf`` JSON file)
  2. ``OPENVIKING_CONFIG_FILE`` env var

Run against TOS::

    OV_GIT_S3_CONF=/path/to/ov.conf \
      python -m pytest tests/agfs/test_git_binding_s3.py -q
"""

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

# Skip the whole module if the native extension is not built locally.
ragfs_python = pytest.importorskip("ragfs_python")


# ---------------- config discovery / skip gating ----------------


def _resolve_conf_path():
    """Resolve an ov.conf path from the documented env-var chain."""
    for candidate in (
        os.getenv("OV_GIT_S3_CONF"),
        os.getenv("OPENVIKING_CONFIG_FILE"),
    ):
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def _load_git_s3_section():
    """Return the ``git`` dict from the resolved ov.conf when it is a usable
    S3 git config, else ``None`` (treated as "skip").
    """
    path = _resolve_conf_path()
    if path is None:
        return None
    try:
        with open(path, "r") as f:
            root = json.load(f)
    except Exception:
        return None

    git = root.get("git") or root.get("storage", {}).get("git")
    if not git:
        return None
    if not git.get("enabled"):
        return None
    if git.get("backend") != "s3":
        return None
    s3 = git.get("s3") or {}
    if not s3.get("bucket") or not s3.get("region"):
        return None
    return git


GIT_S3 = _load_git_s3_section()

pytestmark = pytest.mark.skipif(
    GIT_S3 is None,
    reason="no usable [git.s3] config (set OV_GIT_S3_CONF to an ov.conf with "
    "git.enabled=true, backend='s3')",
)


# ---------------- fixtures ----------------


def _toml_quote(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_bool(v) -> str:
    return "true" if v else "false"


@pytest.fixture
def git_s3_workspace():
    """Create a temp workspace with a ragfs.toml whose [git] backend is s3.

    The git object/ref keys are namespaced under ``{prefix}/_it/{uuid}`` so
    repeated runs never collide. Yields ``(config_path, fs_prefix, s3)`` where
    ``s3`` is the raw s3 config dict and ``fs_prefix`` is a unique prefix to
    use for the s3fs working-tree mount.
    """
    s3 = GIT_S3["s3"]
    run_id = uuid.uuid4().hex
    base_prefix = (s3.get("prefix") or "git").rstrip("/")
    git_prefix = f"{base_prefix}/_it/{run_id}"
    fs_prefix = f"{base_prefix}/_it_fs/{run_id}"

    root = Path(tempfile.mkdtemp(prefix="ov-git-s3-binding-"))
    config_path = root / "ragfs.toml"

    lines = [
        "[git]\n",
        "enabled = true\n",
        'backend = "s3"\n',
        'default_branch = "main"\n',
        'author_name = "test-bot"\n',
        'author_email = "test@example.com"\n',
        "\n",
        "[git.s3]\n",
        f"bucket = {_toml_quote(s3['bucket'])}\n",
        f"region = {_toml_quote(s3['region'])}\n",
        f"prefix = {_toml_quote(git_prefix)}\n",
        f"endpoint = {_toml_quote(s3.get('endpoint', ''))}\n",
    ]
    if s3.get("access_key"):
        lines.append(f"access_key = {_toml_quote(s3['access_key'])}\n")
    if s3.get("secret_key"):
        lines.append(f"secret_key = {_toml_quote(s3['secret_key'])}\n")
    lines.append(f"cas_mode = {_toml_quote(s3.get('cas_mode', 'native'))}\n")
    lines.append(f"use_path_style = {_toml_bool(s3.get('use_path_style', True))}\n")
    config_path.write_text("".join(lines))

    yield config_path, fs_prefix, s3

    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def client(git_s3_workspace):
    config_path, fs_prefix, s3 = git_s3_workspace
    c = ragfs_python.RAGFSBindingClient(git_config_path=str(config_path))
    # Mount s3fs at /local so the working tree lives on the same S3 backend.
    mount_cfg = {
        "bucket": s3["bucket"],
        "region": s3["region"],
        "endpoint": s3.get("endpoint", ""),
        "prefix": fs_prefix,
        "use_path_style": bool(s3.get("use_path_style", True)),
        "disable_ssl": not bool(s3.get("use_ssl", True)),
    }
    if s3.get("access_key"):
        mount_cfg["access_key_id"] = s3["access_key"]
    if s3.get("secret_key"):
        mount_cfg["secret_access_key"] = s3["secret_key"]
    c.mount("s3fs", "/local", mount_cfg)
    return c


# ---------------- helpers ----------------


def _write(client, account: str, rel_path: str, body: bytes) -> str:
    """Write `body` to /local/<account>/<rel_path> via the binding."""
    path = f"/local/{account}/{rel_path}"
    client.ensure_parent_dirs(path)
    client.write(path, body)
    return path


def _acct() -> str:
    """Random account id so concurrent runs never share a namespace."""
    return f"acct-{uuid.uuid4().hex}"


# ---------------- tests ----------------


def test_health_reports_git_backend_s3(client):
    h = client.health()
    assert h["git_enabled"] == "true"
    assert h.get("git_backend") == "s3"


def test_commit_then_show_roundtrip_s3(client):
    """Write a file, commit it, then show it back and verify bytes match."""
    account = _acct()
    body = b"hello viking s3 \x00\x01\x02 binary-ish\n"
    _write(client, account, "resources/a.md", body)

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
    assert len(resp["commit_oid"]) == 40

    shown = client.git_show(
        account=account,
        target_ref="main",
        path="resources/a.md",
    )
    assert shown["bytes"] == body
    assert shown["size"] == len(body)


def test_restore_roundtrip_s3(client):
    """Commit v1 → modify → commit v2 → restore v1 → file reverts; HEAD moves to v3."""
    account = _acct()
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

    # VFS file content reverted on S3.
    assert client.read(f"/local/{account}/resources/proj/a.md") == b"v1-content"

    # Branch now points at the new (forward-only) commit, parented on v2.
    head = client.git_show(account=account, target_ref="main")
    assert head["oid"] == restored["new_commit_oid"]
    assert head["parents"] == [v2_oid]


def test_commit_noop_when_unchanged_s3(client):
    """A second commit with no working-tree change is a Noop on the same HEAD."""
    account = _acct()
    _write(client, account, "resources/a.md", b"stable")
    first = client.git_commit(
        account=account, branch="main", message="first",
        author_name="a", author_email="a@e",
        paths=["resources/a.md"],
    )
    assert first["result"] == "created"

    second = client.git_commit(
        account=account, branch="main", message="second",
        author_name="a", author_email="a@e",
        paths=["resources/a.md"],
    )
    assert second["result"] == "noop"
    assert second["commit_oid"] == first["commit_oid"]


def test_cross_scope_atomic_snapshot_s3(client):
    """A single commit captures files spanning multiple scopes atomically
    (design §15.2): both resources/ and knowledge/ land in one commit tree.
    """
    account = _acct()
    _write(client, account, "resources/doc.md", b"resource body")
    _write(client, account, "knowledge/note.md", b"knowledge body")

    resp = client.git_commit(
        account=account, branch="main", message="snapshot both scopes",
        author_name="a", author_email="a@e",
        paths=["resources/doc.md", "knowledge/note.md"],
    )
    assert resp["result"] == "created"

    assert client.git_show(
        account=account, target_ref="main", path="resources/doc.md"
    )["bytes"] == b"resource body"
    assert client.git_show(
        account=account, target_ref="main", path="knowledge/note.md"
    )["bytes"] == b"knowledge body"


def test_derived_file_rolls_back_with_restore_s3(client):
    """Derived files (e.g. .abstract.md) created after the source commit are
    removed when restoring to that earlier commit (design §15.2).
    """
    account = _acct()
    _write(client, account, "resources/proj/a.md", b"A v1")
    src = client.git_commit(
        account=account, branch="main", message="source",
        author_name="a", author_email="a@e",
        paths=["resources/proj/a.md"],
    )

    # HEAD adds a derived file alongside an edit.
    _write(client, account, "resources/proj/a.md", b"A v2")
    _write(client, account, "resources/proj/.abstract.md", b"derived summary")
    client.git_commit(
        account=account, branch="main", message="head",
        author_name="a", author_email="a@e",
        paths=["resources/proj/a.md", "resources/proj/.abstract.md"],
    )

    restored = client.git_restore(
        account=account, branch="main",
        project_dir="resources/proj",
        source_commit=src["commit_oid"],
        author_name="a", author_email="a@e",
    )
    assert restored["result"] == "applied"
    assert restored["deleted"] >= 1

    # a.md rolled back, derived file gone from the working tree.
    assert client.read(f"/local/{account}/resources/proj/a.md") == b"A v1"
    from openviking.pyagfs import AGFSNotFoundError
    with pytest.raises(AGFSNotFoundError):
        client.read(f"/local/{account}/resources/proj/.abstract.md")
