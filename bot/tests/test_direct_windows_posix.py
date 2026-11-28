# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for the Windows POSIX shim used by Compile sandboxes."""

import asyncio
import os
import shlex
from pathlib import Path

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


def _grants_of(arguments: list[str]) -> list[str]:
    """Extract the principals from an icacls argv (each /grant:r is followed by one)."""
    return [
        value for flag, value in zip(arguments, arguments[1:], strict=False) if flag == "/grant:r"
    ]


def _capture_icacls(monkeypatch, calls) -> None:
    """Stub icacls execution and record the argv that _restrict_windows_acl builds."""

    class _OkProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*arguments, **kwargs):
        calls.append(list(arguments))
        return _OkProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)


@pytest.mark.asyncio
async def test_restrict_windows_acl_drops_machine_account_grant(tmp_path, monkeypatch):
    """A LocalSystem service exposes USERNAME as the machine account `<HOSTNAME>$`.

    icacls cannot resolve that principal to a SID (Win32 error 1332,
    ERROR_NONE_MAPPED), so the grant list must keep only the well-known
    SYSTEM/Administrators SIDs; the service already runs as SYSTEM (#8615).
    """
    calls: list[list[str]] = []
    _capture_icacls(monkeypatch, calls)
    monkeypatch.setenv("USERNAME", "HOST01$")
    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")

    target = str(tmp_path / "credentials")
    os.makedirs(target, exist_ok=True)
    error = await direct._restrict_windows_acl(target, directory=True)

    assert error is None
    assert calls, "icacls should still be invoked"
    grants = _grants_of(calls[0])
    assert grants == ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"]
    assert not any(name.endswith("$") for name, _ in (grant.split(":", 1) for grant in grants))


@pytest.mark.asyncio
async def test_restrict_windows_acl_keeps_interactive_user_grant(tmp_path, monkeypatch):
    """Dev-mode servers run as an interactive, non-Administrator user.

    ``/inheritance:r`` strips inherited owner access, so the account-name grant
    must be preserved in that scenario (regression guard for #8615's caveat).
    """
    calls: list[list[str]] = []
    _capture_icacls(monkeypatch, calls)
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.setenv("USERDOMAIN", "DESKTOP-01")

    target = str(tmp_path / "ovcli.conf")
    Path(target).write_text("{}", encoding="utf-8")
    error = await direct._restrict_windows_acl(target, directory=False)

    assert error is None
    assert _grants_of(calls[0]) == ["DESKTOP-01\\alice:F", "*S-1-5-18:F", "*S-1-5-32-544:F"]


@pytest.mark.asyncio
async def test_restrict_windows_acl_principal_without_domain(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    _capture_icacls(monkeypatch, calls)
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.delenv("USERDOMAIN", raising=False)

    target = str(tmp_path / "credentials")
    os.makedirs(target, exist_ok=True)
    error = await direct._restrict_windows_acl(target, directory=True)

    assert error is None
    assert _grants_of(calls[0]) == [
        "alice:(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F",
    ]


@pytest.mark.asyncio
async def test_restrict_windows_acl_requires_username(tmp_path, monkeypatch):
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USERDOMAIN", raising=False)

    error = await direct._restrict_windows_acl(str(tmp_path), directory=True)

    assert error is not None
    assert "USERNAME is not set" in error


@pytest.mark.asyncio
async def test_compile_credentials_mkdir_uses_sid_only_grants_for_machine_account(
    tmp_path, monkeypatch
):
    """The Compile credentials `mkdir -m 700` path must not pass a machine-account
    principal to icacls, which surfaces as `Compile credentials directory failed`
    with exit code 1332 on LocalSystem deployments (#8615)."""
    calls: list[list[str]] = []
    _capture_icacls(monkeypatch, calls)
    monkeypatch.setenv("USERNAME", "HOST01$")
    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")

    target = shlex.quote(str(tmp_path / ".compile-auth-deadbeef"))
    result = await direct._translate_windows_posix(f"mkdir -m 700 -- {target}", str(tmp_path))

    assert result == "(no output)"
    grants = _grants_of(calls[0])
    assert "$" not in "".join(grants)
    assert grants == ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"]
