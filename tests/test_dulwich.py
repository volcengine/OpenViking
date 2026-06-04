"""Tests for generating a standard-compliant .git directory using dulwich."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_DULWICH_REPO_ROOT = Path(__file__).resolve().parents[2] / "dulwich"
if _DULWICH_REPO_ROOT.exists():
    sys.path.insert(0, str(_DULWICH_REPO_ROOT))

try:
    from dulwich import porcelain
    from dulwich.repo import Repo
except ImportError:  # pragma: no cover - handled by test skip
    porcelain = None
    Repo = None


def _git(*args: str, cwd: Path) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.mark.skipif(Repo is None, reason="dulwich is not available")
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_init_repo_from_directory(tmp_path: Path) -> None:
    """Test initializing a git repo from a directory of files using dulwich."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # Create some test files
    (source_dir / "README.md").write_text("# Test Project\n\nA test project.\n")
    (source_dir / "main.py").write_text("print('hello world')\n")
    nested = source_dir / "src" / "utils"
    nested.mkdir(parents=True)
    (nested / "helper.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    (source_dir / ".gitignore").write_text("__pycache__/\n*.pyc\n")

    # Initialize git repository
    repo = Repo.init(str(source_dir), mkdir=False)

    # Stage all files
    porcelain.add(repo, ".")

    # Commit
    commit_id = porcelain.commit(
        repo,
        message=b"Initial commit",
        author=b"Test Author <test@example.com>",
        committer=b"Test Committer <committer@example.com>",
    )

    assert commit_id is not None
    assert len(commit_id) == 40  # SHA-1 hex length

    # Verify .git directory exists with expected structure
    git_dir = source_dir / ".git"
    assert git_dir.is_dir()
    assert (git_dir / "HEAD").is_file()
    assert (git_dir / "config").is_file()
    assert (git_dir / "objects").is_dir()
    assert (git_dir / "refs").is_dir()
    assert (git_dir / "refs" / "heads").is_dir()

    # Verify repo validity with git fsck
    _git("fsck", cwd=source_dir)

    # Verify commit exists and has correct message
    log_output = _git("log", "--oneline", cwd=source_dir)
    assert "Initial commit" in log_output

    # Verify files are tracked
    ls_files = _git("ls-files", cwd=source_dir).splitlines()
    assert ".gitignore" in ls_files
    assert "README.md" in ls_files
    assert "main.py" in ls_files
    assert "src/utils/helper.py" in ls_files

    # Verify file contents
    show_readme = _git("show", "HEAD:README.md", cwd=source_dir)
    assert show_readme == "# Test Project\n\nA test project."

    show_helper = _git("show", "HEAD:src/utils/helper.py", cwd=source_dir)
    assert "def add(a: int, b: int) -> int:" in show_helper


@pytest.mark.skipif(Repo is None, reason="dulwich is not available")
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_multiple_commits(tmp_path: Path) -> None:
    """Test creating multiple commits in sequence."""
    source_dir = tmp_path / "repo"
    source_dir.mkdir()
    (source_dir / "file1.txt").write_text("content 1\n")

    repo = Repo.init(str(source_dir), mkdir=False)
    porcelain.add(repo, ".")
    commit1 = porcelain.commit(repo, message=b"First commit", author=b"Test <t@e.com>")

    # Add a second commit
    (source_dir / "file2.txt").write_text("content 2\n")
    porcelain.add(repo, "file2.txt")
    commit2 = porcelain.commit(repo, message=b"Second commit", author=b"Test <t@e.com>")

    # Modify an existing file
    (source_dir / "file1.txt").write_text("content 1 updated\n")
    porcelain.add(repo, "file1.txt")
    commit3 = porcelain.commit(repo, message=b"Third commit", author=b"Test <t@e.com>")

    assert commit1 != commit2 != commit3

    log_output = _git("log", "--oneline", cwd=source_dir)
    lines = log_output.splitlines()
    assert len(lines) == 3
    assert "Third commit" in lines[0]
    assert "Second commit" in lines[1]
    assert "First commit" in lines[2]

    # Checkout first commit and verify content
    _git("checkout", commit1.decode(), cwd=source_dir)
    assert (source_dir / "file1.txt").read_text() == "content 1\n"
    assert not (source_dir / "file2.txt").exists()


@pytest.mark.skipif(Repo is None, reason="dulwich is not available")
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_executable_file_mode(tmp_path: Path) -> None:
    """Test that executable files retain their mode in git."""
    source_dir = tmp_path / "repo"
    source_dir.mkdir()

    script = source_dir / "run.sh"
    script.write_text("#!/bin/sh\necho hello\n")
    os.chmod(script, 0o755)

    repo = Repo.init(str(source_dir), mkdir=False)
    porcelain.add(repo, "run.sh")
    porcelain.commit(repo, message=b"Add script", author=b"Test <t@e.com>")

    # Verify git sees the executable mode
    ls_tree = _git("ls-tree", "--long", "HEAD", cwd=source_dir)
    assert "100755" in ls_tree  # Executable mode


@pytest.mark.skipif(Repo is None, reason="dulwich is not available")
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_empty_directory_ignored(tmp_path: Path) -> None:
    """Test that empty directories are not tracked (git behavior)."""
    source_dir = tmp_path / "repo"
    source_dir.mkdir()

    (source_dir / "file.txt").write_text("content\n")
    empty_dir = source_dir / "empty"
    empty_dir.mkdir()

    repo = Repo.init(str(source_dir), mkdir=False)
    porcelain.add(repo, ".")
    porcelain.commit(repo, message=b"Add file", author=b"Test <t@e.com>")

    ls_files = _git("ls-files", cwd=source_dir).splitlines()
    assert "file.txt" in ls_files
    assert not any("empty" in f for f in ls_files)
