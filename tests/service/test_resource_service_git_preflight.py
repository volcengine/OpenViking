# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for Git preflight process cleanup."""

import asyncio
import os
import signal
from unittest.mock import AsyncMock, Mock, call

import pytest

from openviking.service.resource_service import ResourceService
from openviking_cli.exceptions import InvalidArgumentError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")


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
