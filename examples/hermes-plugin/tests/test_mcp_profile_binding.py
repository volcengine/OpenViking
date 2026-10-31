"""MCP launch entries remain bound to the profile that starts the child."""

import argparse
import importlib
import json
import os
import subprocess
import sys

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

    async def serve(resolved):
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

    async def serve(resolved):
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
    assert entry["args"] == ["openviking", "mcp", "--profile-bound"]
    assert "HERMES_HOME" not in entry["env"]
    entry["args"] = ["openviking", "mcp"]
    entry["env"]["HERMES_HOME"] = str(home.parent / "source")
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
    assert adapter.repair_profile_home(home)
    from hermes_cli.config import read_user_config_raw

    saved = read_user_config_raw(home / "config.yaml")["mcp_servers"]["openviking"]
    assert saved["args"] == ["openviking", "mcp", "--profile-bound"]
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
