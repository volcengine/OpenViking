# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for Git preflight process cleanup."""

import asyncio
import base64
import os
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from openviking.parse.mode import ParseMode
from openviking.parse.network import (
    ParserNetworkOperation,
    register_parser_network_adapter,
    reset_parser_network_adapter,
)
from openviking.server.identity import RequestContext, Role
from openviking.service import resource_service as resource_service_module
from openviking.service.resource_service import ResourceService
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")


async def test_account_token_and_parser_proxy_reach_repository_preflight(monkeypatch):
    token_resolver = AsyncMock(return_value="account-token")
    processor = SimpleNamespace(github_token_for=token_resolver)
    service = ResourceService(resource_processor=processor)
    process = Mock(returncode=0)
    head_sha = "a" * 40
    process.communicate = AsyncMock(return_value=(f"{head_sha}\tHEAD\n".encode(), b""))
    execute = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    monkeypatch.setattr(
        resource_service_module, "parse_code_hosting_url", lambda _source: "org/private"
    )
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    monkeypatch.setattr(resource_service_module, "is_github_url", lambda _path: True)
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    register_parser_network_adapter(
        SimpleNamespace(
            supports=lambda context: context.operation is ParserNetworkOperation.GIT_COMMAND,
            subprocess_env_overlay=lambda context: {"HTTPS_PROXY": "http://proxy.example:8118"},
        )
    )
    try:
        plan = await service._prepare_standard_source_plan(
            path="https://github.com/org/private",
            ctx=ctx,
            mode=ParseMode.DEFAULT,
            allow_local_path_resolution=False,
            processor_kwargs={},
            watch_auth_state=None,
        )
    finally:
        reset_parser_network_adapter()

    token_resolver.assert_awaited_once_with("https://github.com/org/private", ctx)
    assert execute.await_args.args == (
        "git",
        "ls-remote",
        "https://github.com/org/private",
        "HEAD",
    )
    env = execute.await_args.kwargs["env"]
    assert env["HTTPS_PROXY"] == "http://proxy.example:8118"
    entries = {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(env["GIT_CONFIG_COUNT"]))
    }
    encoded = base64.b64encode(b"oauth2:account-token").decode("ascii")
    assert entries["http.https://github.com/org/private.extraHeader"] == (
        f"Authorization: Basic {encoded}"
    )
    assert plan is not None
    assert plan.source_identity.head_sha == head_sha
    assert plan.task_auth == {}
    assert "account-token" not in str(plan.processor_args)


@pytest.mark.parametrize(
    "source",
    [
        "git@github.com:org/private.git",
        "ssh://git@github.com/org/private.git",
    ],
)
async def test_default_github_token_is_not_applied_to_ssh_preflight(monkeypatch, source):
    token_resolver = AsyncMock(return_value="account-token")
    processor = SimpleNamespace(github_token_for=token_resolver)
    service = ResourceService(resource_processor=processor)
    service._preflight_git_source = AsyncMock(
        return_value=SimpleNamespace(
            source_name="private",
            source_path=source,
            source_format="repository",
            head_sha=None,
        )
    )
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    monkeypatch.setattr(resource_service_module, "is_github_url", lambda _path: True)
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    plan = await service._prepare_standard_source_plan(
        path=source,
        ctx=ctx,
        mode=ParseMode.DEFAULT,
        allow_local_path_resolution=False,
        processor_kwargs={},
        watch_auth_state=None,
    )

    token_resolver.assert_not_awaited()
    service._preflight_git_source.assert_awaited_once_with(
        source, auth_config=None, branch=None, commit=None
    )
    assert plan is not None


async def test_git_preflight_timeout_kills_process_group_and_bounds_reap(monkeypatch):
    process = Mock(pid=43210)
    process.communicate = AsyncMock(return_value=(b"", b""))
    process.kill = Mock()
    captured = {}
    wait_timeouts = []
    killpg = Mock()

    async def fake_exec(*_args, **kwargs):
        captured.update(kwargs)
        return process

    async def fake_wait_for(awaitable, timeout):
        wait_timeouts.append(timeout)
        if len(wait_timeouts) == 1:
            awaitable.close()
            raise asyncio.TimeoutError
        return await awaitable

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(os, "killpg", killpg)

    with pytest.raises(InvalidArgumentError, match="preflight timed out"):
        await ResourceService()._preflight_git_source("https://github.com/org/private")

    assert (
        captured.get("start_new_session"),
        killpg.call_args_list,
        wait_timeouts,
    ) == (True, [call(process.pid, signal.SIGKILL)], [10.0, 1.0])
    process.kill.assert_not_called()


async def test_git_preflight_cancellation_kills_process_group_and_bounds_reap(monkeypatch):
    process = Mock(pid=43210)
    process.communicate = AsyncMock(side_effect=[asyncio.CancelledError, (b"", b"")])
    process.wait = AsyncMock()
    process.kill = Mock()
    captured = {}
    wait_timeouts = []
    killpg = Mock()

    async def fake_exec(*_args, **kwargs):
        captured.update(kwargs)
        return process

    async def fake_wait_for(awaitable, timeout):
        wait_timeouts.append(timeout)
        return await awaitable

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(os, "killpg", killpg)

    with pytest.raises(asyncio.CancelledError):
        await ResourceService()._preflight_git_source("https://github.com/org/private")

    assert (
        captured.get("start_new_session"),
        killpg.call_args_list,
        wait_timeouts,
    ) == (True, [call(process.pid, signal.SIGKILL)], [10.0, 1.0])
    process.kill.assert_not_called()


async def test_preflight_branched_watch_targets_ref_and_extracts_sha(monkeypatch):
    head_sha = "b" * 40
    process = Mock(returncode=0)
    process.communicate = AsyncMock(
        return_value=(f"{head_sha}\trefs/heads/release-1.0\n".encode(), b"")
    )
    execute = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)

    info = await ResourceService()._preflight_git_source(
        "https://github.com/org/repo.git",
        branch="release-1.0",
    )

    assert execute.await_args.args == (
        "git",
        "ls-remote",
        "--heads",
        "https://github.com/org/repo.git",
        "refs/heads/release-1.0",
    )
    assert info.head_sha == head_sha


async def test_preflight_tree_url_branch_is_resolved_from_url(monkeypatch):
    head_sha = "c" * 40
    process = Mock(returncode=0)
    process.communicate = AsyncMock(
        return_value=(f"{head_sha}\trefs/heads/release-1.0\n".encode(), b"")
    )
    execute = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)

    info = await ResourceService()._preflight_git_source(
        "https://github.com/org/repo/tree/release-1.0",
    )

    assert execute.await_args.args == (
        "git",
        "ls-remote",
        "--heads",
        "https://github.com/org/repo",
        "refs/heads/release-1.0",
    )
    assert info.head_sha == head_sha


async def test_preflight_empty_repository_passes_without_sha(monkeypatch):
    # git ls-remote exits 0 with empty output for an unborn HEAD; preflight must
    # stay advisory and leave the hint unset instead of rejecting the import.
    process = Mock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"", b""))
    execute = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)

    info = await ResourceService()._preflight_git_source(
        "https://github.com/org/empty.git",
    )

    assert execute.await_args.args == (
        "git",
        "ls-remote",
        "https://github.com/org/empty.git",
        "HEAD",
    )
    assert info.head_sha is None


async def test_preflight_commit_pin_checks_head_and_skips_sha_hint(monkeypatch):
    process = Mock(returncode=0)
    process.communicate = AsyncMock(return_value=(f"{'d' * 40}\tHEAD\n".encode(), b""))
    execute = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)

    info = await ResourceService()._preflight_git_source(
        "https://github.com/org/repo.git",
        commit="deadbeef",
    )

    assert execute.await_args.args == (
        "git",
        "ls-remote",
        "https://github.com/org/repo.git",
        "HEAD",
    )
    # The accessor derives resolved_head_sha from the pinned commit itself.
    assert info.head_sha is None
