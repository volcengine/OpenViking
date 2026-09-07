# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for the exec tool working directory."""

from types import SimpleNamespace

import pytest
from vikingbot.agent.tools.shell import ExecTool
from vikingbot.config.schema import SessionKey
from vikingbot.sandbox.backends.direct import DirectBackend


class _SandboxManager:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    async def get_sandbox(self, _session_key):
        return self.sandbox


@pytest.mark.asyncio
async def test_exec_tool_applies_working_dir_for_all_sandbox_backends():
    calls = []

    class Sandbox:
        sandbox_cwd = "/workspace"

        async def execute(self, command, timeout):
            calls.append((command, timeout))
            return "ok"

    context = SimpleNamespace(
        sandbox_manager=_SandboxManager(Sandbox()),
        session_key="session",
    )

    result = await ExecTool(timeout=7).execute(
        context,
        command="pwd",
        working_dir="directory with spaces",
    )

    assert result == "ok"
    assert calls == [("cd 'directory with spaces' && pwd", 7)]


@pytest.mark.asyncio
async def test_exec_tool_pwd_without_working_dir_uses_sandbox_root():
    class Sandbox:
        sandbox_cwd = "/workspace"

        async def execute(self, command, timeout):
            raise AssertionError((command, timeout))

    context = SimpleNamespace(
        sandbox_manager=_SandboxManager(Sandbox()),
        session_key="session",
    )

    result = await ExecTool().execute(context, command="pwd")

    assert result == "/workspace"


@pytest.mark.asyncio
async def test_exec_tool_working_dir_changes_real_command_cwd(tmp_path):
    class Config:
        restrict_workspaces = True

    workspace = tmp_path / "workspace"
    working_dir = workspace / "directory with spaces"
    working_dir.mkdir(parents=True)
    session_key = SessionKey(type="cli", channel_id="default", chat_id="exec-test")
    sandbox = DirectBackend(Config(), session_key, workspace)
    await sandbox.start()
    try:
        context = SimpleNamespace(
            sandbox_manager=_SandboxManager(sandbox),
            session_key=session_key,
        )
        result = await ExecTool().execute(
            context,
            command="pwd",
            working_dir="directory with spaces",
        )
    finally:
        await sandbox.stop()

    assert result.strip() == str(working_dir)
