"""Execution status is independent of text, truncation, and presentation hooks."""

import asyncio
import json
import signal
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.agent.tools.shell import ExecTool
from vikingbot.config.schema import Config, SessionKey
from vikingbot.sandbox.backends.aiosandbox import AioSandboxBackend
from vikingbot.sandbox.backends.direct import DirectBackend
from vikingbot.sandbox.backends.opensandbox import OpenSandboxBackend
from vikingbot.sandbox.backends.srt import SrtBackend
from vikingbot.sandbox.base import CommandResult


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["sleep 2; echo done", "sleep 2 & exit 0"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_direct_terminates_descendants_on_timeout_or_cancel(
    tmp_path, monkeypatch, command, cancel
):
    backend = DirectBackend(Config().sandbox, "test", tmp_path)
    await backend.start()
    created = asyncio.Event()
    processes = []
    create = asyncio.create_subprocess_shell

    async def capture(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        process = await create(*args, **kwargs)
        processes.append(process)
        created.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_shell", capture)
    started = time.monotonic()
    task = asyncio.create_task(backend.execute_result(command, timeout=0.05))
    try:
        if cancel:
            await asyncio.wait_for(created.wait(), 1)
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert not result.success and result.exit_code is None
        assert time.monotonic() - started < 1.5
        assert processes[0].returncode is not None
        # Check the group, not just the shell. Ignore terminated zombies waiting
        # for their parent/init to reap them (common in Linux test containers).
        ps = await asyncio.create_subprocess_exec(
            "ps", "-axo", "pgid=,stat=", stdout=asyncio.subprocess.PIPE
        )
        output, _ = await ps.communicate()
        assert not any(
            int(group) == processes[0].pid and not state.startswith("Z")
            for group, state in (line.split() for line in output.decode().splitlines())
        )
    finally:
        await backend.stop()


@pytest.mark.asyncio
async def test_direct_process_recovery_wait_is_bounded(monkeypatch):
    from vikingbot.sandbox.backends import direct

    killed = []
    monkeypatch.setattr(direct.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(direct, "_PROCESS_CLEANUP_TIMEOUT_SECONDS", 0.02)
    pending = asyncio.Event()
    process = SimpleNamespace(pid=123, communicate=AsyncMock(side_effect=pending.wait))
    started = time.monotonic()
    await DirectBackend._terminate_process_group(process)
    assert time.monotonic() - started < 0.5
    assert killed == [(123, signal.SIGKILL)]


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 7, None])
async def test_opensandbox_real_sdk_command_status_contract(exit_code):
    """Use the installed SDK's real SSE and status clients, not a method stub.

    Run this test with opensandbox==0.1.5 to exercise the declared minimum.
    Only HTTP transport is replaced; no remote service or command is started.
    """
    pytest.importorskip("opensandbox")
    import httpx
    from opensandbox.adapters.command_adapter import CommandsAdapter
    from opensandbox.config import ConnectionConfig
    from opensandbox.models.sandboxes import SandboxEndpoint

    requests = []

    def respond(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/command":
            events = [
                {"type": "init", "text": "cmd-1", "timestamp": 1},
                {"type": "stdout", "text": "done", "timestamp": 2},
                {"type": "execution_complete", "timestamp": 3, "execution_time": 1},
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="".join(f"data: {json.dumps(event)}\n\n" for event in events),
            )
        assert request.method == "GET" and request.url.path == "/command/status/cmd-1"
        return httpx.Response(200, json={"id": "cmd-1", "running": False, "exit_code": exit_code})

    transport = httpx.MockTransport(respond)
    adapter = CommandsAdapter(
        ConnectionConfig(transport=transport), SandboxEndpoint(endpoint="sdk.test")
    )
    backend = object.__new__(OpenSandboxBackend)
    backend._sandbox = SimpleNamespace(commands=adapter)
    try:
        result = await backend.execute_result("echo done")
        assert result.output.startswith("done")
        assert result.exit_code == exit_code
        assert result.success is (exit_code == 0)
        assert requests == [("POST", "/command"), ("GET", "/command/status/cmd-1")]
    finally:
        await adapter._httpx_client.aclose()
        await adapter._sse_client.aclose()
        await transport.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 1, None])
@pytest.mark.parametrize("backend_type", [AioSandboxBackend, OpenSandboxBackend, SrtBackend])
async def test_remote_command_status_is_not_inferred_from_output(backend_type, exit_code):
    backend = object.__new__(backend_type)
    output = "x" * 12000
    if backend_type is AioSandboxBackend:
        backend._client = SimpleNamespace(
            shell=SimpleNamespace(
                exec_command=AsyncMock(
                    return_value=SimpleNamespace(
                        data=SimpleNamespace(output=output, exit_code=exit_code),
                    )
                ),
            )
        )
    elif backend_type is OpenSandboxBackend:
        pytest.importorskip("opensandbox.models.execd")
        backend._sandbox = SimpleNamespace(
            commands=SimpleNamespace(
                run=AsyncMock(
                    return_value=SimpleNamespace(
                        id="execution",
                        error=None,
                        logs=SimpleNamespace(stdout=[SimpleNamespace(text=output)], stderr=[]),
                    )
                ),
                get_command_status=AsyncMock(
                    return_value=SimpleNamespace(
                        running=False,
                        exit_code=exit_code,
                    )
                ),
            )
        )
    else:
        backend._process = object()
        backend._send_message = AsyncMock()
        backend._wait_for_response = AsyncMock(
            return_value={
                "type": "executed",
                "stdout": output,
                "exitCode": exit_code,
            }
        )
    result = await backend.execute_result("command")
    assert "truncated" in result.output
    assert result.exit_code == exit_code
    assert result.success is (exit_code == 0)
    assert await backend.execute("command") == result.output
    if backend_type is OpenSandboxBackend:
        backend._sandbox.commands.get_command_status.assert_awaited_with("execution")


@pytest.mark.asyncio
async def test_opensandbox_execution_error_cannot_be_success():
    pytest.importorskip("opensandbox.models.execd")
    backend = object.__new__(OpenSandboxBackend)
    backend._sandbox = SimpleNamespace(
        commands=SimpleNamespace(
            run=AsyncMock(
                return_value=SimpleNamespace(
                    id="execution",
                    error=SimpleNamespace(value="Execution failed"),
                    logs=None,
                )
            ),
            get_command_status=AsyncMock(return_value=SimpleNamespace(running=False, exit_code=0)),
        )
    )
    assert not (await backend.execute_result("command")).success


@pytest.mark.asyncio
async def test_direct_output_is_not_status(tmp_path):
    backend = DirectBackend(Config().sandbox, "test", tmp_path)
    await backend.start()
    try:
        result = await backend.execute_result("printf 'Error: this is data'; exit 0")
        assert result.success and result.output == "Error: this is data"
        result = await backend.execute_result("exit 7")
        assert not result.success and result.exit_code == 7
        # exec replaces the shell, so timeout leaves no child process behind.
        result = await backend.execute_result("exec sleep 2", timeout=0.01)
        assert not result.success and result.exit_code is None
    finally:
        await backend.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 1, None])
async def test_post_hook_cannot_rewrite_execution_status(monkeypatch, exit_code):
    from vikingbot.hooks.manager import hook_manager

    async def hooks(*, result=None, **kwargs):
        return {"result": "Reformatted output"}

    monkeypatch.setattr(hook_manager, "execute_hooks", hooks)
    sandbox = SimpleNamespace(
        execute_result=AsyncMock(return_value=CommandResult("output", exit_code))
    )
    manager = SimpleNamespace(
        get_sandbox=AsyncMock(return_value=sandbox), to_workspace_id=lambda _: "test"
    )
    registry = ToolRegistry(Config())
    registry.register(ExecTool())
    result = await registry.execute_detailed(
        "exec",
        {"command": "command"},
        session_key=SessionKey(type="test", channel_id="test", chat_id="test"),
        sandbox_manager=manager,
    )
    assert result.success is (exit_code == 0)
    assert result.result == "Reformatted output"
