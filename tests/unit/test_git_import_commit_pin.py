# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for Git import commit-pin validation."""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from openviking.parse.accessors.git_accessor import GitAccessor, _normalize_commit
from openviking_cli.exceptions import InvalidArgumentError


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("deadbeef", "deadbeef"),
        ("DEADBEEF", "deadbeef"),
        ("a" * 40, "a" * 40),
    ],
)
def test_normalize_commit_accepts_sha_shapes(value, expected):
    assert _normalize_commit(value) == expected


@pytest.mark.parametrize(
    "value",
    ["deadbe", "z" * 7, "-f", "--upload-pack=evil", "refs/heads/main", 123],
)
def test_normalize_commit_rejects_non_sha_values(value):
    with pytest.raises(InvalidArgumentError):
        _normalize_commit(value)


@pytest.mark.asyncio
async def test_invalid_commit_is_rejected_before_git_runs(monkeypatch):
    accessor = GitAccessor()
    run_git = AsyncMock()
    monkeypatch.setattr(accessor, "_run_git", run_git)

    with pytest.raises(InvalidArgumentError):
        await accessor.access("https://github.com/org/repo.git", commit="12345")

    run_git.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_commit_rejects_nonmatching_object_id(monkeypatch):
    accessor = GitAccessor()
    monkeypatch.setattr(accessor, "_run_git", AsyncMock(return_value="a" * 40))

    assert await accessor._resolve_commit("repo", "deadbeef") is None


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_git_clone_checks_out_full_and_abbreviated_sha(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "audit@example.invalid")
    _git(repo, "config", "user.name", "Audit")
    (repo / "tracked.txt").write_text("first\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "first")
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "tracked.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "second")
    second = _git(repo, "rev-parse", "HEAD")

    accessor = GitAccessor()
    for index, pin in enumerate((first, first[:8])):
        target = tmp_path / f"clone-{index}"
        await accessor._git_clone(str(repo), str(target), commit=pin)
        assert _git(target, "rev-parse", "HEAD") == first
        assert _git(target, "rev-parse", "HEAD") != second

    branch_target = tmp_path / "clone-branch"
    await accessor._git_clone(str(repo), str(branch_target), branch="main")
    assert _git(branch_target, "rev-parse", "HEAD") == second
