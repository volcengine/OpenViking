#!/usr/bin/env python3
"""Demo: Monorepo per-directory commits using pure dulwich (no git CLI).

Each subdirectory (project) can be committed and rolled back independently,
while sharing a single .git directory at the repo root.
"""

import os
import stat
import sys
import tempfile
from pathlib import Path

_DULWICH_REPO_ROOT = Path(__file__).resolve().parents[2] / "dulwich"
if _DULWICH_REPO_ROOT.exists():
    sys.path.insert(0, str(_DULWICH_REPO_ROOT))

from dulwich import porcelain
from dulwich.objects import Commit, Tree
from dulwich.repo import Repo


def init_repo(root: str) -> Repo:
    """Initialize a monorepo at root."""
    root_path = Path(root).resolve()
    if (root_path / ".git").exists():
        raise ValueError(f".git already exists in {root_path}")
    repo = Repo.init(str(root_path), mkdir=False)
    print(f"✓ Initialized repo at {root_path}")
    return repo


def commit_project(repo: Repo, project_dir: str, message: str) -> str:
    """Commit only changes under project_dir.

    Args:
        repo: Dulwich Repo instance
        project_dir: Relative path to the project directory (e.g. "projectA")
        message: Commit message

    Returns:
        Commit hash as hex string
    """
    porcelain.add(repo, project_dir)
    commit_id = porcelain.commit(
        repo,
        message=message.encode("utf-8"),
        author=b"User <user@example.com>",
        committer=b"User <user@example.com>",
    )
    commit_hex = commit_id.decode("ascii")
    print(f"✓ [{project_dir}] {message} -> {commit_hex[:8]}")
    return commit_hex


def _walk_tree(repo: Repo, tree: Tree, prefix: str = "") -> dict[str, tuple[int, bytes]]:
    """Recursively walk a tree, returning {path: (mode, sha)} for all blobs."""
    entries = {}
    for name, mode, sha in tree.iteritems():
        name_str = name.decode("utf-8")
        full_path = f"{prefix}/{name_str}" if prefix else name_str
        if stat.S_ISDIR(mode):
            subtree = repo[sha]
            entries.update(_walk_tree(repo, subtree, full_path))
        else:
            entries[full_path] = (mode, sha)
    return entries


def rollback_project(repo: Repo, project_dir: str, to_commit: str) -> None:
    """Roll back a single project directory to a previous commit (pure dulwich).

    This only affects the specified project directory; other projects
    remain unchanged.
    """
    root = Path(repo.path)
    project_prefix = project_dir.rstrip("/")
    target_commit = repo[to_commit.encode("ascii")]
    target_tree = repo[target_commit.tree]

    # Collect all files in the target commit under project_dir
    target_files = _walk_tree(repo, target_tree)
    target_project_files = {
        path: (mode, sha)
        for path, (mode, sha) in target_files.items()
        if path == project_prefix or path.startswith(f"{project_prefix}/")
    }

    # Collect currently tracked files under project_dir (from HEAD)
    head = repo[repo.head()]
    head_tree = repo[head.tree]
    current_files = _walk_tree(repo, head_tree)
    current_project_files = {
        path: (mode, sha)
        for path, (mode, sha) in current_files.items()
        if path == project_prefix or path.startswith(f"{project_prefix}/")
    }

    # Delete files that exist now but didn't exist in the target commit
    for path in current_project_files:
        if path not in target_project_files:
            file_path = root / path
            if file_path.exists():
                file_path.unlink()

    # Restore files from target commit
    for path, (mode, sha) in target_project_files.items():
        file_path = root / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        blob = repo[sha]
        file_path.write_bytes(blob.data)
        os.chmod(file_path, mode & 0o777)

    # Stage and commit the rollback
    porcelain.add(repo, project_dir)
    commit_id = porcelain.commit(
        repo,
        message=f"Rollback {project_dir} to {to_commit[:8]}".encode("utf-8"),
        author=b"User <user@example.com>",
        committer=b"User <user@example.com>",
    )
    print(f"↩ [{project_dir}] Rolled back to {to_commit[:8]} -> {commit_id.decode()[:8]}")


def get_file_at_commit(repo: Repo, file_path: str, commit_sha: str) -> bytes:
    """Get the content of a file at a specific commit.

    Args:
        repo: Dulwich Repo instance
        file_path: Path to the file relative to repo root (e.g. "projectA/file.txt")
        commit_sha: Hex SHA of the commit to read from

    Returns:
        Raw file content as bytes
    """
    commit = repo[commit_sha.encode("ascii")]
    tree = repo[commit.tree]

    # Walk the tree to find the file
    parts = file_path.split("/")
    current_tree = tree
    for part in parts[:-1]:
        mode, sha = current_tree[part.encode("utf-8")]
        current_tree = repo[sha]

    filename = parts[-1].encode("utf-8")
    mode, blob_sha = current_tree[filename]
    blob = repo[blob_sha]
    return blob.data


def log_project(repo: Repo, project_dir: str) -> None:
    """Show commit history for a single project (pure dulwich)."""
    project_bytes = project_dir.encode("utf-8")
    walker = repo.get_walker(paths=[project_bytes])

    print(f"\n=== History: {project_dir} ===")
    count = 0
    for entry in walker:
        commit = entry.commit
        short_sha = commit.id.decode("ascii")[:8]
        message = commit.message.decode("utf-8", errors="replace").split("\n")[0]
        print(f"{short_sha} {message}")
        count += 1
    if count == 0:
        print("(no commits)")


def main() -> int:
    # Create a temp monorepo structure
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "monorepo"
        root.mkdir()

        # Create 3 project directories with initial files
        for name in ["projectA", "projectB", "projectC"]:
            proj = root / name
            proj.mkdir()
            (proj / "file.txt").write_text(f"{name} v1\n")

        print(f"=== Test monorepo at {root} ===\n")

        # Step 1: Initialize repo
        repo = init_repo(str(root))

        # Step 2: Commit each project independently
        commit_a1 = commit_project(repo, "projectA", "projectA: initial")
        commit_b1 = commit_project(repo, "projectB", "projectB: initial")
        commit_c1 = commit_project(repo, "projectC", "projectC: initial")

        # Step 3: Modify and commit projectA again
        (root / "projectA" / "file.txt").write_text("projectA v2\n")
        commit_a2 = commit_project(repo, "projectA", "projectA: update to v2")

        # Step 4: Add new file to projectB
        (root / "projectB" / "new.txt").write_text("new file in B\n")
        commit_b2 = commit_project(repo, "projectB", "projectB: add new.txt")

        # Step 5: Show per-project history (pure dulwich)
        log_project(repo, "projectA")
        log_project(repo, "projectB")
        log_project(repo, "projectC")

        # Step 6: Roll back projectA to v1 (other projects unaffected)
        print(f"\n=== Rolling back projectA to {commit_a1[:8]} (v1) ===")
        rollback_project(repo, "projectA", commit_a1)

        # Step 7: Read file content at specific commits
        print(f"\n=== Reading historical versions ===")
        content_a1 = get_file_at_commit(repo, "projectA/file.txt", commit_a1).decode()
        content_a2 = get_file_at_commit(repo, "projectA/file.txt", commit_a2).decode()
        print(f"projectA/file.txt @ commit_a1 ({commit_a1[:8]}): {repr(content_a1.strip())}")
        print(f"projectA/file.txt @ commit_a2 ({commit_a2[:8]}): {repr(content_a2.strip())}")

        # Verify content
        print(f"\n=== Verification ===")
        print(f"projectA/file.txt: {repr((root / 'projectA' / 'file.txt').read_text().strip())}")
        print(f"projectB/file.txt: {repr((root / 'projectB' / 'file.txt').read_text().strip())}")
        print(f"projectB/new.txt exists: {(root / 'projectB' / 'new.txt').exists()}")
        print(f"projectC/file.txt: {repr((root / 'projectC' / 'file.txt').read_text().strip())}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
