"""MCP launch entries remain bound to the profile that starts the child."""

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("profile_bound,home_set", [(False, True), (True, False)])
def test_unbound_launch_refuses_before_connection(
    external_provider, monkeypatch, profile_bound, home_set
):
    home, _, module, _ = external_provider("launch-guard")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    if home_set:
        monkeypatch.setenv("HERMES_HOME", str(home))
    else:
        monkeypatch.delenv("HERMES_HOME")
    contacted = []

    async def serve(resolved, **_kwargs):
        contacted.append(resolved)

    monkeypatch.setattr(adapter, "serve", serve)
    with pytest.raises(SystemExit):
        adapter.run(argparse.Namespace(profile_bound=profile_bound))
    assert contacted == []


def test_bound_launch_uses_supplied_profile_home(external_provider, monkeypatch):
    home, _, module, _ = external_provider("bound-launch")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    monkeypatch.setenv("HERMES_HOME", str(home))
    contacted = []

    async def serve(resolved, **_kwargs):
        contacted.append(resolved)

    monkeypatch.setattr(adapter, "serve", serve)
    adapter.run(argparse.Namespace(profile_bound=True))
    assert contacted == [home.resolve()]


def test_new_entry_and_legacy_migration_preserve_profile_policy(external_provider):
    home, _, module, _ = external_provider("portable-entry")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {
        "mcp_servers": {
            "openviking": {
                "trust": "full",
                "enabled": False,
                "tools": {"exclude": ["forget"]},
                "headers": {"X-Gateway": "${GATEWAY_SECRET}"},
            }
        }
    }
    adapter.configure(config, str(home))
    entry = config["mcp_servers"]["openviking"]
    assert entry["args"][-3:] == ["openviking", "mcp", "--profile-bound"]
    assert "HERMES_HOME" not in entry["env"]
    launcher = [entry["command"], *entry["args"]]
    entry["command"] = "hermes"
    entry["args"] = ["openviking", "mcp"]
    entry["env"]["HERMES_HOME"] = str(home.parent / "source")
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
    assert adapter.repair_profile_home(home)
    from hermes_cli.config import read_user_config_raw

    saved = read_user_config_raw(home / "config.yaml")["mcp_servers"]["openviking"]
    assert [saved["command"], *saved["args"]] == launcher
    assert "HERMES_HOME" not in saved["env"]
    for key in ("trust", "enabled", "tools", "headers"):
        assert saved[key] == entry[key]
    assert adapter.repair_profile_home(home) is None


def test_adapter_process_refuses_missing_profile_home(external_provider, tmp_path):
    _, _, module, _ = external_provider("launch-process")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    # Deliberately omit HERMES_HOME. The guard must run in the child process
    # before provider imports or HTTP, under the actual adapter entry point.
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    result = subprocess.run(
        [sys.executable, adapter.__file__, "--profile-bound"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Hermes did not supply the MCP profile home" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("external_install", [False, True])
def test_saved_launcher_uses_current_installation_with_stale_path(
    external_provider, monkeypatch, tmp_path, external_install
):
    from hermes_cli import _launchers
    from tools.mcp_tool_config import _resolve_stdio_command

    home, _, module, _ = external_provider("installation-launch")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    if external_install:
        monkeypatch.setattr(_launchers, "resolve_store_python", lambda *_: None)
    stale_bin = tmp_path / "stale-bin"
    stale_bin.mkdir()
    stale = stale_bin / ("hermes.cmd" if os.name == "nt" else "hermes")
    stale.write_text(
        "@echo off\necho STALE_HERMES\nexit /b 91\n"
        if os.name == "nt"
        else "#!/bin/sh\necho STALE_HERMES\nexit 91\n",
        encoding="utf-8",
    )
    stale.chmod(0o755)
    monkeypatch.setenv("PATH", str(stale_bin) + os.pathsep + os.environ.get("PATH", ""))
    config = {}
    adapter.configure(config, str(home))
    entry = config["mcp_servers"]["openviking"]
    assert Path(entry["command"]).is_absolute()
    assert ".venv" not in Path(entry["command"]).parts
    assert "HERMES_HOME" not in entry["env"]
    resolved, _ = _resolve_stdio_command(entry["command"], {"PATH": os.environ["PATH"]})
    assert resolved == entry["command"]
    assert Path(resolved) != stale
    if external_install:
        assert resolved == sys._base_executable
        assert entry["args"][:2] == ["-I", "-c"]


@pytest.mark.parametrize("launch", ["bare", "source", "windows-source", "external"])
def test_launcher_migration_keeps_profile_and_mcp_policy(external_provider, launch):
    from hermes_cli._launchers import runtime_command
    from hermes_cli.config import read_user_config_raw

    home, _, module, _ = external_provider("launcher-migration")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {}
    adapter.configure(config, str(home))
    expected = config["mcp_servers"]["openviking"].copy()
    old_root = home.parent / "old-installation"
    bound_args = ["openviking", "mcp", "--profile-bound"]
    if launch == "external":
        command = runtime_command(old_root, bound_args, python=sys._base_executable)
    elif launch == "windows-source":
        command = [r"C:\old-installation\.hermes\bin\hermes.exe", *bound_args]
    else:
        command = ["hermes" if launch == "bare" else str(old_root / ".hermes/bin/hermes")]
        command += bound_args
    entry = config["mcp_servers"]["openviking"]
    entry.update(command=command[0], args=command[1:])
    policy = {
        "enabled": False,
        "trust": "untrusted",
        "connect_timeout": 91,
        "timeout": 123,
        "tools": {"include": ["read"], "exclude": ["forget"]},
        "headers": {"X-Gateway": "${GATEWAY_SECRET}"},
        "ssl_verify": "/custom/ca.pem",
    }
    entry.update(policy)
    config["mcp_servers"]["other"] = {"command": "custom-server", "args": ["serve"]}
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
    assert adapter.repair_profile_home(home)
    saved = read_user_config_raw(home / "config.yaml")["mcp_servers"]
    for key in ("command", "args", "env"):
        assert saved["openviking"][key] == expected[key]
    for key, value in policy.items():
        assert saved["openviking"][key] == value
    assert saved["other"] == config["mcp_servers"]["other"]
    before = (home / "config.yaml").read_bytes()
    assert adapter.repair_profile_home(home) is None
    assert (home / "config.yaml").read_bytes() == before


@pytest.mark.parametrize("startup_elapsed,attempts", [(0, 1), (2, 1), (0, 4)])
def test_discovery_failure_reaches_host_before_its_deadline(
    external_provider, monkeypatch, startup_elapsed, attempts
):
    import asyncio

    import httpx2

    home, _, module, _ = external_provider("host-discovery-deadline")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    connection = adapter.Connection(home, module)
    values, entry, managed = connection.resolve()
    entry["connect_timeout"] = 4
    monkeypatch.setattr(connection, "resolve", lambda: (values, entry, managed))
    contacted = []

    async def unavailable(method, params):
        contacted.append((method, params))
        raise httpx2.ConnectError("Connection refused")

    monkeypatch.setattr(connection, "request", unavailable)

    async def probe():
        async with asyncio.timeout(4 - startup_elapsed) as host_deadline:
            with pytest.raises(TimeoutError) as caught:
                await connection.discover(None, startup_elapsed=startup_elapsed, attempts=attempts)
            assert not host_deadline.expired()
            assert "Cannot connect to OpenViking" in adapter._error_hint(caught.value)

    asyncio.run(probe())
    assert contacted and all(call == ("tools/list", None) for call in contacted)


@pytest.mark.parametrize("wrapper", [False, True])
def test_launch_age_includes_only_adapter_launcher_parents(external_provider, monkeypatch, wrapper):
    import psutil

    _, _, module, _ = external_provider("launcher-age")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    caller = SimpleNamespace(
        cmdline=lambda: ["hermes", "mcp", "test", "openviking"], create_time=lambda: 50
    )
    launcher = SimpleNamespace(
        cmdline=lambda: ["hermes", "openviking", "mcp", "--profile-bound"], create_time=lambda: 80
    )
    process = SimpleNamespace(
        create_time=lambda: 90, parents=lambda: [launcher, caller] if wrapper else [caller]
    )
    monkeypatch.setattr(psutil, "Process", lambda: process)
    monkeypatch.setattr(adapter.time, "time", lambda: 100)
    assert adapter._launch_age() == (20 if wrapper else 10)


def test_launch_age_metadata_failure_keeps_discovery_available(external_provider, monkeypatch):
    import psutil

    _, _, module, _ = external_provider("launcher-age-error")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")

    def denied():
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "Process", denied)
    monkeypatch.setattr(adapter.time, "time", lambda: 100)
    assert adapter._launch_age() == 0
