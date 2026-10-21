# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for agent command access and working directories."""

from types import SimpleNamespace

import pytest
from vikingbot.agent.tools.factory import register_default_tools
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.config.schema import Config, SessionKey
from vikingbot.sandbox.backends.direct import DirectBackend


class _SandboxManager:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    async def get_sandbox(self, _session_key):
        return self.sandbox

    def to_workspace_id(self, _session_key):
        return "exec-test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("relative_dir", "allow_exec"),
    [(None, True), ("directory with spaces", True), (None, False)],
)
async def test_exec_tool_runs_in_selected_directory(tmp_path, relative_dir, allow_exec):
    config = Config()
    if not allow_exec:
        config.sandbox.backends.direct.allow_exec = False
    registry = ToolRegistry(config)
    register_default_tools(registry, config)

    workspace = tmp_path / "workspace"
    working_dir = workspace / relative_dir if relative_dir else workspace
    working_dir.mkdir(parents=True)
    session_key = SessionKey(type="cli", channel_id="default", chat_id="exec-test")
    sandbox = DirectBackend(config.sandbox, session_key, workspace)
    await sandbox.start()
    try:
        result = await registry.execute(
            "exec",
            {
                "command": "python3 -c 'import os; print(os.getcwd())'",
                **({"working_dir": relative_dir} if relative_dir else {}),
            },
            session_key=session_key,
            sandbox_manager=_SandboxManager(sandbox),
        )
    finally:
        await sandbox.stop()

    if allow_exec:
        assert result.strip() == str(working_dir.resolve())
    else:
        assert result == "Error: Tool 'exec' not found"


def test_compile_exec_requires_opt_in():
    from vikingbot.compile.service import BotCompileService

    service = SimpleNamespace(config=Config())
    assert not BotCompileService._compile_capabilities(service).exec_enabled
    service.config.sandbox.backends.direct.allow_compile_exec = True
    assert BotCompileService._compile_capabilities(service).exec_enabled
    service.config.sandbox.backends.direct.allow_exec = False
    assert not BotCompileService._compile_capabilities(service).exec_enabled
