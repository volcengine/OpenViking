# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for the Windows POSIX shim used by Compile sandboxes."""

import asyncio
import shlex

import pytest
from vikingbot.compile.sandbox import _CompileSandbox
from vikingbot.config.schema import Config
from vikingbot.sandbox.backends import direct


class _FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"ok", b""


async def _acl_ok(path, *, directory):
    return None


@pytest.fixture
def windows_backend(tmp_path, monkeypatch):
    """A DirectBackend forced through the Windows code path with a stubbed shell."""
    monkeypatch.setattr(direct.os, "name", "nt")
    calls = []

    async def fake_create_subprocess_shell(command, **kwargs):
        calls.append((command, kwargs.get("env")))
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    backend = direct.DirectBackend(Config().sandbox, "test", tmp_path)
    backend._running = True
    return backend, calls


@pytest.mark.asyncio
async def test_initialize_handles_posix_quoted_paths(tmp_path, monkeypatch):
    workspace = tmp_path / "Anne O'Brien & R&D work"
    workspace.mkdir()

    class Backend:
        sandbox_cwd = str(workspace)

        async def execute(self, command, timeout=60, **kwargs):
            return await direct._translate_windows_posix(command, self.sandbox_cwd)

        async def write_file(self, path, content):
            (workspace / path).write_text(content, encoding="utf-8")

        @staticmethod
        def _ensure_command_succeeded(output, operation):
            direct.DirectBackend._ensure_command_succeeded(output, operation)

    monkeypatch.setattr(direct, "_restrict_windows_acl", _acl_ok)
    sandbox = _CompileSandbox(Backend())
    await sandbox.initialize({"url": "http://127.0.0.1:1933"})

    credential_dir = workspace / sandbox._directory
    assert credential_dir.is_dir()
    assert (credential_dir / "ovcli.conf").is_file()


@pytest.mark.asyncio
async def test_initialize_and_execute_use_child_env(windows_backend, monkeypatch):
    backend, calls = windows_backend
    monkeypatch.setenv("OPENVIKING_URL", "http://host-override")
    monkeypatch.setattr(direct, "_restrict_windows_acl", _acl_ok)
    sandbox = _CompileSandbox(backend)

    await sandbox.initialize({"url": "http://127.0.0.1:1933"})
    result = await sandbox.execute('python -c "print(123)"')

    assert result == "ok"
    command, env = calls[-1]
    assert command == 'python -c "print(123)"'
    assert "unset" not in command and "OPENVIKING_CLI_CONFIG_FILE=" not in command
    assert env["OPENVIKING_CLI_CONFIG_FILE"] == sandbox._config_path
    assert "OPENVIKING_URL" not in env


@pytest.mark.asyncio
async def test_direct_execute_moves_env_prefix_into_child_env(windows_backend):
    backend, calls = windows_backend
    command = (
        "unset OPENVIKING_URL OPENVIKING_API_KEY; "
        "export OPENVIKING_CLI_CONFIG_FILE='C:\\work\\ovcli.conf'; "
        'python -c "print(123)"'
    )

    result = await backend.execute(command)

    assert result == "ok"
    captured, env = calls[-1]
    assert captured == 'python -c "print(123)"'
    assert env["OPENVIKING_CLI_CONFIG_FILE"] == r"C:\work\ovcli.conf"
    assert "OPENVIKING_URL" not in env and "OPENVIKING_API_KEY" not in env


@pytest.mark.asyncio
async def test_chmod_only_supports_exact_form(tmp_path, monkeypatch):
    target = tmp_path / "ovcli.conf"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(direct, "_restrict_windows_acl", _acl_ok)
    quoted = shlex.quote(str(target))

    result = await direct._translate_windows_posix(f"chmod 600 {quoted}", str(tmp_path))
    assert result == "(no output)"
    assert await direct._translate_windows_posix(f"chmod 000 {quoted}", str(tmp_path)) is None
    assert (
        await direct._translate_windows_posix(f"chmod 600 {quoted} {quoted}", str(tmp_path)) is None
    )
    assert await direct._translate_windows_posix(f"chmod -R 600 {quoted}", str(tmp_path)) is None


@pytest.mark.asyncio
async def test_mkdir_rejects_unsupported_flags(tmp_path):
    target = shlex.quote(str(tmp_path / "credentials"))

    result = await direct._translate_windows_posix(f"mkdir -p {target}", str(tmp_path))
    assert result == "(no output)"
    assert await direct._translate_windows_posix(f"mkdir -m 755 -- {target}", str(tmp_path)) is None
    assert (
        await direct._translate_windows_posix(f"mkdir -p {target} && echo ok", str(tmp_path))
        is None
    )
    assert await direct._translate_windows_posix("echo hi", str(tmp_path)) is None
