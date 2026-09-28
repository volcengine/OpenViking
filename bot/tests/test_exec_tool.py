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
@pytest.mark.parametrize("relative_dir", [None, "directory with spaces"])
async def test_exec_tool_runs_in_selected_directory(tmp_path, relative_dir):
    class Config:
        restrict_workspaces = True

    workspace = tmp_path / "workspace"
    working_dir = workspace / relative_dir if relative_dir else workspace
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
            command="python3 -c 'import os; print(os.getcwd())'",
            working_dir=relative_dir,
        )
    finally:
        await sandbox.stop()

    assert result.strip() == str(working_dir)
