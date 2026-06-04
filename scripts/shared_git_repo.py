#!/usr/bin/env python3
"""Manage multiple work trees sharing a single git data directory using dulwich."""

import argparse
import os
import sys
from pathlib import Path

_DULWICH_REPO_ROOT = Path(__file__).resolve().parents[2] / "dulwich"
if _DULWICH_REPO_ROOT.exists():
    sys.path.insert(0, str(_DULWICH_REPO_ROOT))

from dulwich import porcelain
from dulwich.repo import Repo


def _write_git_pointer(work_dir: Path, git_dir: Path) -> None:
    """Write a .git pointer file in work_dir pointing to git_dir."""
    git_file = work_dir / ".git"
    if git_file.exists():
        raise ValueError(f"{git_file} already exists")
    git_file.write_text(f"gitdir: {git_dir}\n")


def init_shared_repo(git_data_dir: str) -> Path:
    """Initialize a shared bare git repository.

    Args:
        git_data_dir: Path where git data will be stored

    Returns:
        Resolved path to the git data directory
    """
    git_dir = Path(git_data_dir).resolve()
    if git_dir.exists():
        raise ValueError(f"Git data directory already exists: {git_dir}")
    git_dir.mkdir(parents=True)
    Repo.init_bare(str(git_dir), mkdir=False)
    print(f"✓ Created shared git data directory: {git_dir}")
    return git_dir


def add_worktree(work_dir: str, git_data_dir: str, message: str | None = None) -> str:
    """Add a work tree to an existing shared git repo and commit its contents.

    Args:
        work_dir: Path to the directory to track
        git_data_dir: Path to the shared git data directory
        message: Optional commit message (defaults to "Add <dirname>")

    Returns:
        The commit hash as a hex string
    """
    work_path = Path(work_dir).resolve()
    git_dir = Path(git_data_dir).resolve()

    if not work_path.is_dir():
        raise ValueError(f"Work directory does not exist: {work_path}")
    if not git_dir.is_dir():
        raise ValueError(f"Git data directory does not exist: {git_dir}")

    # Write .git pointer
    _write_git_pointer(work_path, git_dir)

    # Open repo with work tree context
    repo = Repo(str(work_path))

    commit_msg = message or f"Add {work_path.name}"
    print(f"Committing {work_path} -> {commit_msg}")

    porcelain.add(repo, ".")
    commit_id = porcelain.commit(
        repo,
        message=commit_msg.encode("utf-8"),
        author=b"User <user@example.com>",
        committer=b"User <user@example.com>",
    )

    commit_hex = commit_id.decode("ascii")
    print(f"✓ Committed: {commit_hex}")
    return commit_hex


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage multiple work trees sharing one git data directory"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init: create shared git data dir
    init_parser = subparsers.add_parser("init", help="Initialize a shared git data directory")
    init_parser.add_argument("git_data_dir", help="Path to store shared git data")

    # add: add a work tree
    add_parser = subparsers.add_parser("add", help="Add a work tree and commit its contents")
    add_parser.add_argument("work_dir", help="Directory to track")
    add_parser.add_argument("git_data_dir", help="Shared git data directory")
    add_parser.add_argument("-m", "--message", help="Commit message")

    args = parser.parse_args()

    try:
        if args.command == "init":
            init_shared_repo(args.git_data_dir)
        elif args.command == "add":
            add_worktree(args.work_dir, args.git_data_dir, args.message)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
