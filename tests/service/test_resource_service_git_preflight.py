# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for Git preflight process cleanup."""

import asyncio
import os
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from openviking.parse.mode import ParseMode
from openviking.server.identity import RequestContext, Role
from openviking.service import resource_service as resource_service_module
from openviking.service.resource_service import ResourceService
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")


async def test_account_github_token_is_used_for_repository_preflight(monkeypatch):
    token_resolver = AsyncMock(return_value="account-token")
    processor = SimpleNamespace(github_token_for=token_resolver)
    service = ResourceService(resource_processor=processor)
    service._preflight_git_source = AsyncMock(
        return_value=SimpleNamespace(
            source_name="private",
            source_path="https://github.com/org/private",
            source_format="repository",
        )
    )
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: True)
    monkeypatch.setattr(resource_service_module, "is_github_url", lambda _path: True)
    ctx = RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )

    plan = await service._prepare_standard_source_plan(
        path="https://github.com/org/private",
        ctx=ctx,
        mode=ParseMode.DEFAULT,
        allow_local_path_resolution=False,
        processor_kwargs={},
        watch_auth_state=None,
    )

    token_resolver.assert_awaited_once_with("https://github.com/org/private", ctx)
    preflight_auth = service._preflight_git_source.await_args.kwargs["auth_config"]
    assert preflight_auth.username == "oauth2"
    assert preflight_auth.token == "account-token"
    assert plan is not None
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
    service._preflight_git_source.assert_awaited_once_with(source, auth_config=None)
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
