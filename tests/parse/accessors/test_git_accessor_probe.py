# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the lightweight git ls-remote watch probe."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.parse.accessors import git_accessor
from openviking.parse.accessors.git_accessor import GitAccessor
from openviking.parse.network import (
    ParserNetworkOperation,
    register_parser_network_adapter,
    reset_parser_network_adapter,
)

_resolve_probe_target = GitAccessor._resolve_probe_target
_parse_ls_remote_ref = GitAccessor._parse_ls_remote_ref


def probe_git_remote_head(source, **kwargs):
    return GitAccessor().probe_git_remote_head(source, **kwargs)


class FakeProc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.pid = 12345
        self._stdout = stdout
        self._stderr = stderr
        self.calls = []

    async def communicate(self):
        self.calls.append("communicate")
        return self._stdout, self._stderr


class TestProbeTargetResolution:
    def test_explicit_branch_kept_for_plain_clone_url(self):
        url, branch, commit = _resolve_probe_target(
            "https://github.com/volcengine/OpenViking.git", branch="dev"
        )
        assert url == "https://github.com/volcengine/OpenViking.git"
        assert branch == "dev"
        assert commit is None

    def test_explicit_commit_wins_over_branch(self):
        url, branch, commit = _resolve_probe_target(
            "https://github.com/volcengine/OpenViking.git",
            branch="main",
            commit="a" * 40,
        )
        assert url == "https://github.com/volcengine/OpenViking.git"
        assert branch is None
        assert commit == "a" * 40

    def test_tree_url_branch_is_resolved(self):
        url, branch, commit = _resolve_probe_target(
            "https://github.com/volcengine/OpenViking/tree/release-1.0"
        )
        assert url == "https://github.com/volcengine/OpenViking"
        assert branch == "release-1.0"
        assert commit is None

    def test_commit_pin_short_circuits(self):
        url, branch, commit = _resolve_probe_target(
            "https://github.com/volcengine/OpenViking/commit/abcdef1234567890"
        )
        assert commit == "abcdef1234567890"
        assert branch is None

    def test_explicit_commit_overrides_url_commit(self):
        url, branch, commit = _resolve_probe_target(
            "https://github.com/volcengine/OpenViking/commit/abcdef1234567890",
            commit="deadbeef",
        )
        assert url == "https://github.com/volcengine/OpenViking"
        assert branch is None
        assert commit == "deadbeef"

    def test_ssh_url_passthrough(self):
        source = "git@github.com:volcengine/OpenViking.git"
        url, branch, commit = _resolve_probe_target(source, ref="main")
        assert url == source
        assert branch == "main"

    def test_non_git_source_returns_none(self, tmp_path):
        assert _resolve_probe_target(str(tmp_path / "repo")) is None


class TestLsRemoteOutputParsers:
    def test_heads_output(self):
        stdout = f"{'a' * 40}\trefs/heads/main\n{'b' * 40}\trefs/heads/dev\n"
        assert _parse_ls_remote_ref(stdout, "refs/heads/main") == "a" * 40
        assert _parse_ls_remote_ref(stdout, "refs/heads/release") is None

    def test_head_output(self):
        stdout = f"{'c' * 40}\tHEAD\n"
        assert _parse_ls_remote_ref(stdout, "HEAD") == "c" * 40
        assert _parse_ls_remote_ref(stdout, "refs/heads/main") is None


class TestProbeGitRemoteHead:
    @pytest.mark.asyncio
    async def test_branch_without_hint_downloads_by_name_without_checkpoint(
        self, monkeypatch
    ):
        # Without a commit pin or git_head_sha_hint the accessor must NOT issue
        # its own ls-remote: it downloads the moving branch by name and records
        # no exact-revision checkpoint (fail-open to the legacy path).
        accessor = GitAccessor()
        local_dir = Path("/tmp/openviking-test-repository")
        zip_download = AsyncMock(return_value=(local_dir, "volcengine/OpenViking"))
        monkeypatch.setattr(accessor, "_github_zip_download", zip_download)
        monkeypatch.setattr(
            accessor,
            "probe_git_remote_head",
            AsyncMock(side_effect=AssertionError("accessor must not self-probe")),
        )

        resource = await accessor.access(
            "https://github.com/volcengine/OpenViking.git",
            branch="main",
        )

        assert zip_download.await_args.args[1] == "main"
        assert resource.path == local_dir
        assert resource.meta.get("resolved_head_sha") is None

    @pytest.mark.asyncio
    async def test_probe_applies_parser_network_adapter(self, monkeypatch):
        accessor = GitAccessor()
        run_git = AsyncMock(return_value=f"{'a' * 40}\trefs/heads/main\n")
        monkeypatch.setattr(accessor, "_run_git", run_git)
        register_parser_network_adapter(
            SimpleNamespace(
                supports=lambda context: context.operation is ParserNetworkOperation.GIT_COMMAND,
                subprocess_env_overlay=lambda _context: {"OV_GIT_ROUTE": "adapter"},
            )
        )
        try:
            result = await accessor.probe_git_remote_head(
                "https://github.com/volcengine/OpenViking.git",
                branch="main",
            )
        finally:
            reset_parser_network_adapter()

        assert result == ("refs/heads/main", "a" * 40)
        assert run_git.await_args.kwargs["env"]["OV_GIT_ROUTE"] == "adapter"

    @pytest.mark.asyncio
    async def test_known_branch_uses_heads_refspec(self, monkeypatch):
        captured = {}
        fake_proc = FakeProc(stdout=f"{'a' * 40}\trefs/heads/main\n".encode())

        async def fake_exec(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return fake_proc

        monkeypatch.setattr(git_accessor.asyncio, "create_subprocess_exec", fake_exec)

        ref, sha = await probe_git_remote_head(
            "https://github.com/volcengine/OpenViking.git", branch="main"
        )

        assert ref == "refs/heads/main"
        assert sha == "a" * 40
        git_args = captured["args"]
        assert git_args[:3] == ("git", "ls-remote", "--heads")
        assert git_args[-1] == "refs/heads/main"

    @pytest.mark.asyncio
    async def test_unknown_branch_tracks_remote_head(self, monkeypatch):
        captured = {}
        fake_proc = FakeProc(stdout=f"{'d' * 40}\tHEAD\n".encode())

        async def fake_exec(*args, **kwargs):
            captured["args"] = args
            return fake_proc

        monkeypatch.setattr(git_accessor.asyncio, "create_subprocess_exec", fake_exec)

        ref, sha = await probe_git_remote_head(
            "https://github.com/volcengine/OpenViking.git"
        )

        assert (ref, sha) == ("HEAD", "d" * 40)
        git_args = captured["args"]
        assert git_args[:3] == ("git", "ls-remote", "https://github.com/volcengine/OpenViking.git")
        assert git_args[-1] == "HEAD"
        assert "--symref" not in git_args

    @pytest.mark.asyncio
    async def test_commit_pin_returns_supplied_commit_without_subprocess(self, monkeypatch):
        async def fake_exec(*_args, **_kwargs):
            raise AssertionError("must not spawn git for a commit pin")

        monkeypatch.setattr(git_accessor.asyncio, "create_subprocess_exec", fake_exec)

        result = await probe_git_remote_head(
            "https://github.com/volcengine/OpenViking/commit/abcdef1234567890"
        )
        assert result == ("", "abcdef1234567890")

    @pytest.mark.asyncio
    async def test_non_git_source_returns_none(self, monkeypatch, tmp_path):
        result = await probe_git_remote_head(str(tmp_path / "repo"))
        assert result is None

    @pytest.mark.asyncio
    async def test_git_failure_raises(self, monkeypatch):
        fake_proc = FakeProc(returncode=1, stderr=b"fatal: not found")

        async def fake_exec(*_args, **_kwargs):
            return fake_proc

        monkeypatch.setattr(git_accessor.asyncio, "create_subprocess_exec", fake_exec)

        with pytest.raises(RuntimeError):
            await probe_git_remote_head(
                "https://github.com/volcengine/OpenViking.git", branch="main"
            )

    @pytest.mark.asyncio
    async def test_missing_ref_in_output_raises(self, monkeypatch):
        fake_proc = FakeProc(stdout=b"")

        async def fake_exec(*_args, **_kwargs):
            return fake_proc

        monkeypatch.setattr(git_accessor.asyncio, "create_subprocess_exec", fake_exec)

        with pytest.raises(RuntimeError, match="did not advertise"):
            await probe_git_remote_head(
                "https://github.com/volcengine/OpenViking.git", branch="ghost"
            )
