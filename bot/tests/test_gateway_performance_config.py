# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Execute gateway startup with synthetic config and mocked long-running owners."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from typer.testing import CliRunner
from vikingbot.cli import commands
from vikingbot.config import loader

from openviking.observability.usage_audit import runtime as audit_runtime


def write_config(path, *, enabled):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "bot": {
                    "gateway": {"host": "127.0.0.1", "port": 18790},
                    "sandbox": {"backend": "direct"},
                },
                "server": {"observability": {"usage_audit": {"performance_enabled": enabled}}},
            }
        )
    )


@pytest.fixture
def gateway_owners(monkeypatch):
    async def wait_until_cancelled():
        await asyncio.Event().wait()

    manager = SimpleNamespace(cleanup_all=AsyncMock())
    agent = SimpleNamespace(
        sandbox_manager=manager,
        run=AsyncMock(side_effect=wait_until_cancelled),
        close_mcp=AsyncMock(),
    )
    channels = SimpleNamespace(start_all=AsyncMock(side_effect=wait_until_cancelled))
    heartbeat = SimpleNamespace(start=AsyncMock(side_effect=wait_until_cancelled), stop=Mock())
    compile_service = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
    sandbox_runtime = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    startup = Mock()
    monkeypatch.setattr(commands, "validate_openviking_auth", lambda config: None)
    monkeypatch.setattr(commands, "_abort_if_port_in_use", lambda *args: None)
    monkeypatch.setattr(commands, "_init_bot_data", lambda config: None)
    monkeypatch.setattr(commands, "SessionManager", lambda path: object())
    monkeypatch.setattr(commands, "prepare_cron", lambda config, bus: None)
    monkeypatch.setattr(commands, "prepare_agent_loop", lambda *args: agent)
    monkeypatch.setattr(commands, "prepare_channel", lambda *args, **kwargs: channels)
    monkeypatch.setattr(commands, "prepare_heartbeat", lambda *args: heartbeat)
    monkeypatch.setattr(
        "vikingbot.compile.service.BotCompileService", lambda **kwargs: compile_service
    )
    monkeypatch.setattr(
        "vikingbot.sandbox.runtime.OpenSandboxRuntime", lambda config: sandbox_runtime
    )
    monkeypatch.setattr("vikingbot.utils.startup.report_startup", startup)
    monkeypatch.setattr(commands.signal, "signal", lambda *args: None)

    class Server:
        started = True

        def __init__(self, config):
            self.config = config

        async def serve(self):
            await asyncio.sleep(0)

    monkeypatch.setattr("uvicorn.Config", lambda app, **kwargs: SimpleNamespace(app=app, **kwargs))
    monkeypatch.setattr("uvicorn.Server", Server)
    bootstrap = AsyncMock()
    shutdown = AsyncMock()
    monkeypatch.setattr(audit_runtime, "init_usage_audit_from_server_config", bootstrap)
    monkeypatch.setattr(audit_runtime, "shutdown_usage_audit", shutdown)
    monkeypatch.setattr("openviking.observability.usage_audit.shutdown_usage_audit", shutdown)
    actual_init = audit_runtime.init_bot_usage_audit
    selected = []

    async def record_init(path, *, app):
        selected.append((Path(path), app))
        return await actual_init(path, app=app)

    monkeypatch.setattr(audit_runtime, "init_bot_usage_audit", record_init)
    return SimpleNamespace(
        bootstrap=bootstrap,
        shutdown=shutdown,
        selected=selected,
        startup=startup,
        sandbox=sandbox_runtime,
        compile=compile_service,
        agent=agent,
        manager=manager,
    )


@pytest.mark.parametrize(
    "default_exists,selected_enabled", [(False, True), (True, True), (True, False)]
)
def test_gateway_explicit_config_is_used_by_performance_bootstrap(
    tmp_path, monkeypatch, gateway_owners, default_exists, selected_enabled
):
    selected = tmp_path / "selected" / "ov.conf"
    default = tmp_path / "default" / "ov.conf"
    write_config(selected, enabled=selected_enabled)
    if default_exists:
        write_config(default, enabled=not selected_enabled)
    monkeypatch.setattr(loader, "CONFIG_PATH", None)
    monkeypatch.setattr(loader, "get_config_path", lambda: default)
    monkeypatch.setattr(commands, "get_config_path", lambda: default)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(commands.app, ["gateway", "--config", "selected/ov.conf"])
    assert result.exit_code == 0, result.output
    [(observed, app)] = gateway_owners.selected
    assert observed == selected.resolve()
    assert default.exists() is default_exists
    if selected_enabled:
        gateway_owners.bootstrap.assert_awaited_once()
        config = gateway_owners.bootstrap.await_args.args[0]
        assert config.observability.usage_audit.performance_enabled is True
        assert gateway_owners.bootstrap.await_args.kwargs["app"] is app
    else:
        gateway_owners.bootstrap.assert_not_awaited()
    gateway_owners.shutdown.assert_awaited_once_with(app=app)
    gateway_owners.sandbox.start.assert_awaited_once()
    gateway_owners.sandbox.stop.assert_awaited_once()
    gateway_owners.compile.close.assert_awaited_once()
    gateway_owners.manager.cleanup_all.assert_awaited_once()


def test_gateway_default_missing_config_keeps_creation_and_disabled_bootstrap(
    tmp_path, monkeypatch, gateway_owners
):
    default = tmp_path / "default" / "ov.conf"
    monkeypatch.setattr(loader, "CONFIG_PATH", None)
    monkeypatch.setattr(loader, "get_config_path", lambda: default)
    monkeypatch.setattr(commands, "get_config_path", lambda: default)
    assert not default.exists()
    result = CliRunner().invoke(commands.app, ["gateway"])
    assert result.exit_code == 0, result.output
    assert default.exists()
    [(observed, app)] = gateway_owners.selected
    assert observed == default.resolve()
    gateway_owners.bootstrap.assert_not_awaited()
    gateway_owners.shutdown.assert_awaited_once_with(app=app)
    gateway_owners.sandbox.stop.assert_awaited_once()


def test_gateway_failed_performance_bootstrap_runs_shutdown_without_starting_owners(
    tmp_path, monkeypatch, gateway_owners
):
    selected = tmp_path / "selected.conf"
    write_config(selected, enabled=True)
    monkeypatch.setattr(loader, "CONFIG_PATH", None)
    monkeypatch.setattr(loader, "get_config_path", lambda: selected)
    monkeypatch.setattr(commands, "get_config_path", lambda: selected)
    gateway_owners.bootstrap.side_effect = RuntimeError("synthetic bootstrap failure")
    result = CliRunner().invoke(commands.app, ["gateway", "--config", str(selected)])
    assert result.exit_code == 1
    [(observed, app)] = gateway_owners.selected
    assert observed == selected.resolve()
    gateway_owners.sandbox.start.assert_not_awaited()
    gateway_owners.compile.start.assert_not_awaited()
    gateway_owners.shutdown.assert_awaited_once_with(app=app)
    gateway_owners.sandbox.stop.assert_awaited_once()


def test_gateway_freezes_default_resolution_before_starting_services(
    tmp_path, monkeypatch, gateway_owners
):
    selected = tmp_path / "selected.conf"
    later_default = tmp_path / "later-default.conf"
    write_config(selected, enabled=False)
    write_config(later_default, enabled=True)
    resolver = Mock(side_effect=[selected, later_default])
    monkeypatch.setattr(loader, "CONFIG_PATH", None)
    monkeypatch.setattr(loader, "get_config_path", resolver)
    monkeypatch.setattr(commands, "get_config_path", resolver)
    result = CliRunner().invoke(commands.app, ["gateway"])
    assert result.exit_code == 0, result.output
    [(observed, app)] = gateway_owners.selected
    assert observed == selected.resolve()
    assert resolver.call_count == 1
    gateway_owners.bootstrap.assert_not_awaited()
    gateway_owners.shutdown.assert_awaited_once_with(app=app)
