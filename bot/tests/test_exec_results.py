"""Command status is metadata, never inferred from text printed by a process."""

import asyncio
import copy
import json
import shlex
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from vikingbot.agent.loop import _is_tool_result_success
from vikingbot.agent.tools.base import Tool
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.config.schema import Config, SessionKey
from vikingbot.sandbox.backends.aiosandbox import AioSandboxBackend
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.sandbox.backends.opensandbox import OpenSandboxBackend
from vikingbot.sandbox.base import CommandResult, SandboxBackend


@pytest.mark.asyncio
async def test_timeout_remains_failed_when_process_exits_during_cleanup(tmp_path, monkeypatch):
    config = Config(storage_workspace=str(tmp_path))
    backend = DirectBackend(config.sandbox, "status", tmp_path)
    await backend.start()
    process = SimpleNamespace(
        pid=12345,
        returncode=0,
        communicate=AsyncMock(side_effect=asyncio.TimeoutError),
        wait=AsyncMock(return_value=0),
        kill=Mock(side_effect=ProcessLookupError),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_shell", AsyncMock(return_value=process))
    monkeypatch.setattr("os.killpg", Mock(side_effect=ProcessLookupError), raising=False)
    result = await backend.execute("worker")
    assert not _is_tool_result_success(result)
    assert "timed out" in result
    await backend.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("print('Error: literal output\\nExit code: 9')", 0),
        ("print('all good\\n' + 'x'*17000); raise SystemExit(7)", 7),
    ],
)
async def test_direct_exit_status_survives_arbitrary_and_truncated_output(tmp_path, code, status):
    config = Config(storage_workspace=str(tmp_path))
    backend = DirectBackend(config.sandbox, "status", tmp_path / "commands")
    await backend.start()
    try:
        result = await backend.execute(shlex.join([sys.executable, "-c", code]))
        assert isinstance(result, str)
        assert result.exit_code == status
        assert _is_tool_result_success(result) == (status == 0)
        if status == 0:
            SandboxBackend._ensure_command_succeeded(result, "successful command")
        else:
            with pytest.raises(OSError):
                SandboxBackend._ensure_command_succeeded(result, "failed command")
        assert copy.deepcopy(result).exit_code == status
        assert json.loads(json.dumps(result)) == str(result)
    finally:
        await backend.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["opensandbox", "aiosandbox"])
@pytest.mark.parametrize("status", [0, 7])
async def test_remote_backends_preserve_structured_status(tmp_path, kind, status):
    config = Config(storage_workspace=str(tmp_path), sandbox={"backend": kind})
    text = "Error: a literal message" if status == 0 else "all good"
    if kind == "opensandbox":
        backend = OpenSandboxBackend(config.sandbox, "status", tmp_path / "unused")
        # SDK 0.1.5 reports process failure through CommandExecError, not exit_code.
        execution = SimpleNamespace(
            logs=SimpleNamespace(stdout=[SimpleNamespace(text=text)], stderr=[]),
            error=SimpleNamespace(name="CommandExecError", value=str(status)) if status else None,
        )
        backend._sandbox = SimpleNamespace(
            commands=SimpleNamespace(run=AsyncMock(return_value=execution))
        )
    else:
        backend = AioSandboxBackend(config.sandbox, "status", tmp_path / "unused")
        backend._client = SimpleNamespace(
            shell=SimpleNamespace(
                exec_command=AsyncMock(
                    return_value=SimpleNamespace(
                        data=SimpleNamespace(output=text, exit_code=status)
                    )
                )
            )
        )
    result = await backend.execute("command")
    assert result.exit_code == status
    assert _is_tool_result_success(result) == (status == 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [0, 7])
async def test_tool_observability_uses_exit_status(monkeypatch, status):
    class Probe(Tool):
        name = "exec"
        description = "Return command output."
        parameters = {"type": "object", "properties": {}}

        async def execute(self, tool_context, **kwargs):
            return CommandResult("Error: literal" if status == 0 else "all good", status)

    registry = ToolRegistry()
    registry.register(Probe())
    context = MagicMock()
    registry.langfuse = SimpleNamespace(
        enabled=True, tool_call=Mock(return_value=context), end_tool_call=Mock(), flush=Mock()
    )

    async def unchanged_hook(**kwargs):
        return {"result": kwargs["result"]}

    monkeypatch.setattr(
        "vikingbot.agent.tools.registry.hook_manager.execute_hooks",
        AsyncMock(side_effect=unchanged_hook),
    )
    outcome = await registry.execute_detailed(
        "exec", {}, session_key=SessionKey(type="cli", channel_id="status", chat_id="test")
    )
    assert outcome.result.exit_code == status
    assert registry.langfuse.end_tool_call.call_args.kwargs["success"] == (status == 0)
