#!/usr/bin/env python3
"""Initialize a git repository in a directory using dulwich."""

import argparse
import os
import sys
from pathlib import Path

_DULWICH_REPO_ROOT = Path(__file__).resolve().parents[2] / "dulwich"
if _DULWICH_REPO_ROOT.exists():
    sys.path.insert(0, str(_DULWICH_REPO_ROOT))

from dulwich import porcelain
from dulwich.repo import Repo


def init_git_repo(
    target_dir: str,
    message: str = "Initial commit",
    separate_git_dir: str | None = None,
) -> str:
    """Initialize a git repository in target_dir and commit all files.

    Args:
        target_dir: Path to the directory to initialize
        message: Commit message
        separate_git_dir: Optional path to store .git data outside the work tree

    Returns:
        The commit hash as a hex string
    """
    target_path = Path(target_dir).resolve()
    if not target_path.is_dir():
        raise ValueError(f"Directory does not exist: {target_path}")

    if separate_git_dir:
        git_dir = Path(separate_git_dir).resolve()
        if git_dir.exists():
            raise ValueError(f"Git directory already exists at {git_dir}")
        git_dir.mkdir(parents=True)
        worktree_git_file = target_path / ".git"
        if worktree_git_file.exists():
            raise ValueError(f"{worktree_git_file} already exists")
        # Initialize bare repo in separate location
        repo = Repo.init_bare(str(git_dir), mkdir=False)
        # Write .git pointer file in work tree
        worktree_git_file.write_text(f"gitdir: {git_dir}\n")
        # Reopen repo with work tree context
        from dulwich.repo import Repo as _Repo

        repo = _Repo(str(target_path))
        actual_git_dir = git_dir
    else:
        git_dir = target_path / ".git"
        if git_dir.exists():
            raise ValueError(f"Git repository already exists at {git_dir}")
        repo = Repo.init(str(target_path), mkdir=False)
        actual_git_dir = git_dir

    print(f"Initializing git repository in {target_path}...")
    print(f"Git data directory: {actual_git_dir}")

    print("Staging all files...")
    porcelain.add(repo, ".")

    print("Committing...")
    commit_id = porcelain.commit(
        repo,
        message=message.encode("utf-8"),
        author=b"User <user@example.com>",
        committer=b"User <user@example.com>",
    )

    commit_hex = commit_id.decode("ascii")
    print(f"✓ Committed: {commit_hex}")
    return commit_hex


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize a git repo using dulwich")
    parser.add_argument("directory", help="Target directory to initialize")
    parser.add_argument("-m", "--message", default="Initial commit", help="Commit message")
    parser.add_argument(
        "--separate-git-dir",
        help="Store .git data in this directory instead of inside the work tree",
    )
    args = parser.parse_args()

    try:
        init_git_repo(args.directory, args.message, args.separate_git_dir)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
