from __future__ import annotations

import shutil
import stat as statmod
import subprocess
import sys
import time
from pathlib import Path

import pytest

from openviking import AsyncOpenViking
from openviking.storage.viking_fs import get_viking_fs


_DULWICH_REPO_ROOT = Path(__file__).resolve().parents[3] / "dulwich"
if _DULWICH_REPO_ROOT.exists():
    sys.path.insert(0, str(_DULWICH_REPO_ROOT))

try:
    from dulwich.objects import Blob, Commit, Tree
except ImportError:  # pragma: no cover - handled by test skip
    Blob = Commit = Tree = None  # type: ignore[assignment]


async def _write_loose_object(viking_fs, git_dir_uri: str, obj) -> bytes:
    """Write one Dulwich object as a loose git object under .git/objects."""
    hexsha = obj.id.decode("ascii")
    object_uri = f"{git_dir_uri}/objects/{hexsha[:2]}/{hexsha[2:]}"
    await viking_fs.write_file_bytes(object_uri, obj.as_legacy_object())
    return obj.id


def _git_mode_from_entry(entry: dict) -> int:
    raw_mode = int(entry.get("mode", 0) or 0)
    return 0o100755 if raw_mode & 0o111 else 0o100644


async def _build_tree_from_vikingfs(viking_fs, root_uri: str, git_dir_uri: str) -> bytes:
    tree = Tree()
    entries = await viking_fs.ls(root_uri, show_all_hidden=True)

    for entry in sorted(entries, key=lambda item: item["name"]):
        name = entry["name"]
        if name in {".", "..", ".git"}:
            continue

        child_uri = f"{root_uri.rstrip('/')}/{name}"
        if entry.get("isDir", False):
            subtree_id = await _build_tree_from_vikingfs(viking_fs, child_uri, git_dir_uri)
            tree.add(name.encode("utf-8"), statmod.S_IFDIR, subtree_id)
        else:
            blob = Blob.from_string(await viking_fs.read_file_bytes(child_uri))
            await _write_loose_object(viking_fs, git_dir_uri, blob)
            tree.add(name.encode("utf-8"), _git_mode_from_entry(entry), blob.id)

    await _write_loose_object(viking_fs, git_dir_uri, tree)
    return tree.id


async def _snapshot_vikingfs_directory_to_git(
    viking_fs,
    repo_uri: str,
    *,
    branch: str = "main",
    message: bytes = b"snapshot from vikingfs",
) -> bytes:
    git_dir_uri = f"{repo_uri.rstrip('/')}/.git"
    await viking_fs.mkdir(git_dir_uri, exist_ok=True)
    await viking_fs.mkdir(f"{git_dir_uri}/objects", exist_ok=True)
    await viking_fs.mkdir(f"{git_dir_uri}/refs", exist_ok=True)
    await viking_fs.mkdir(f"{git_dir_uri}/refs/heads", exist_ok=True)
    await viking_fs.mkdir(f"{git_dir_uri}/refs/tags", exist_ok=True)

    await viking_fs.write_file(
        f"{git_dir_uri}/config",
        "[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tfilemode = true\n",
    )

    tree_id = await _build_tree_from_vikingfs(viking_fs, repo_uri, git_dir_uri)

    now = int(time.time())
    commit = Commit()
    commit.tree = tree_id
    commit.author = b"OpenViking Test <test@example.com>"
    commit.committer = b"OpenViking Test <test@example.com>"
    commit.author_time = now
    commit.commit_time = now
    commit.author_timezone = 0
    commit.commit_timezone = 0
    commit.parents = []
    commit.message = message

    await _write_loose_object(viking_fs, git_dir_uri, commit)
    await viking_fs.write_file(f"{git_dir_uri}/refs/heads/{branch}", commit.id + b"\n")
    await viking_fs.write_file(f"{git_dir_uri}/HEAD", f"ref: refs/heads/{branch}\n")

    return commit.id


def _extract_local_path(stat_result: dict) -> Path:
    meta = stat_result.get("meta")
    content = meta.get("Content") if isinstance(meta, dict) else None
    local_path = content.get("local_path") if isinstance(content, dict) else None
    assert local_path, f"stat() did not expose local_path in meta.Content: {stat_result!r}"
    return Path(local_path)


@pytest.mark.asyncio
async def test_snapshot_vikingfs_directory_to_git_and_show_file(tmp_path: Path):
    if Blob is None or Commit is None or Tree is None:
        pytest.skip("dulwich is not available for this test environment")

    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    await AsyncOpenViking.reset()
    client = AsyncOpenViking(path=str(tmp_path / "ov-data"))
    await client.initialize()
    try:
        viking_fs = get_viking_fs()
        repo_uri = "viking://resources/git_snapshot_demo"
        nested_uri = f"{repo_uri}/nested"

        await viking_fs.mkdir(repo_uri, exist_ok=True)
        await viking_fs.mkdir(nested_uri, exist_ok=True)
        await viking_fs.write_file(f"{repo_uri}/hello.txt", "hello from vikingfs\n")
        await viking_fs.write_file(f"{nested_uri}/data.txt", "nested payload\n")

        commit_id = (await _snapshot_vikingfs_directory_to_git(viking_fs, repo_uri)).decode("ascii")

        repo_stat = await viking_fs.stat(repo_uri)
        local_repo_path = _extract_local_path(repo_stat)

        result = subprocess.run(
            ["git", "-C", str(local_repo_path), "show", f"{commit_id}:nested/data.txt"],
            check=True,
            capture_output=True,
            text=True,
        )

        assert result.stdout == "nested payload\n"
    finally:
        await client.close()
        await AsyncOpenViking.reset()
