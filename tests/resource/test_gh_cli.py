# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.resource.github_radar.gh_cli import GhCli, GhCliError


def test_local_timeout_is_retryable():
    assert GhCli._retryable(
        GhCliError("gh timed out after 120.0s: gh api repos/volcengine/OpenViking/issues")
    )


def test_rate_limited_403_is_retryable():
    assert GhCli._retryable(
        GhCliError("gh exited 1: HTTP 403: API rate limit exceeded for user")
    )


def test_plain_403_is_not_retryable():
    # Permission errors are permanent; retrying would just burn the attempt budget.
    assert not GhCli._retryable(
        GhCliError("gh exited 1: 403 You are not authorized to perform this action")
    )


def test_status_code_classification_is_unchanged():
    assert GhCli._retryable(GhCliError("gh exited 1: 429 too many requests"))
    assert GhCli._retryable(GhCliError("gh exited 1: 502 Bad Gateway"))
    assert not GhCli._retryable(GhCliError("gh exited 1: 401 Bad credentials"))


class _FakeProc:
    def __init__(self) -> None:
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    async def communicate(self):
        raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_cancellation_kills_child_and_propagates(monkeypatch):
    proc = _FakeProc()

    async def _fake_exec(*argv, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    with pytest.raises(asyncio.CancelledError):
        await GhCli(timeout_seconds=1.0)._run(["gh", "api", "repos/x/y/issues"])

    # The child must be terminated instead of leaking after cancellation.
    assert proc.killed is True
