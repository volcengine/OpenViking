# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cron is opt-in for both tool registration and scheduler startup."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from vikingbot.agent.tools.factory import register_default_tools, register_subagent_tools
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.bus.queue import MessageBus
from vikingbot.cli import commands
from vikingbot.config import loader
from vikingbot.config.schema import Config, SessionKey
from vikingbot.cron.service import CronService
from vikingbot.cron.types import CronSchedule


@pytest.mark.parametrize("enabled", [None, False, True])
def test_cron_config_load_and_save(tmp_path, monkeypatch, enabled):
    path = tmp_path / "ov.conf"
    tools = {} if enabled is None else {"cron": {"enabled": enabled}}
    path.write_text(json.dumps({"bot": {"tools": tools}}))
    monkeypatch.setattr(loader, "CONFIG_PATH", path)

    config = loader.load_config()
    assert config.tools.cron.enabled is (enabled is True)
    loader.save_config(config, path)
    assert loader.load_config().tools.cron.enabled is (enabled is True)


@pytest.mark.parametrize("enabled", [False, True])
def test_cron_tool_registration(tmp_path, enabled):
    config = Config(tools={"cron": {"enabled": enabled}})
    registry = ToolRegistry(config=config)
    service = CronService(tmp_path / "jobs.json")
    register_default_tools(registry, config, cron_service=service)
    assert registry.has("cron") is enabled
    assert registry.has("exec")
    assert registry.has("read_file")
    names = {tool["function"]["name"] for tool in registry.get_definitions()}
    assert ("cron" in names) is enabled

    subagent = ToolRegistry(config=config)
    register_subagent_tools(subagent, config)
    assert not subagent.has("cron")

    without_service = ToolRegistry(config=config)
    register_default_tools(without_service, config)
    assert not without_service.has("cron")


def test_disabled_cron_preserves_existing_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(commands, "get_data_dir", lambda: tmp_path)
    path = tmp_path / "cron" / "jobs.json"
    service = CronService(path)
    service.add_job(
        "reminder",
        CronSchedule(kind="every", every_ms=60_000),
        "hello",
        SessionKey(type="cli", channel_id="test", chat_id="test"),
    )
    before = path.read_bytes()
    constructor = Mock(side_effect=AssertionError("Disabled cron must not be constructed"))
    monkeypatch.setattr(commands, "CronService", constructor)

    assert commands.prepare_cron(Config(), MessageBus()) is None
    constructor.assert_not_called()
    assert path.read_bytes() == before


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("mode", ["gateway", "interactive", "single_turn", "eval"])
def test_startup_respects_cron_config(tmp_path, monkeypatch, enabled, mode):
    config = Config(storage_workspace=str(tmp_path), tools={"cron": {"enabled": enabled}})
    agent = SimpleNamespace(run=AsyncMock(), close_mcp=AsyncMock())
    channels = SimpleNamespace(start_all=AsyncMock())
    cron = SimpleNamespace(start=AsyncMock(), status=lambda: {"jobs": 0})
    constructor = Mock(return_value=cron)
    prepare_agent = Mock(return_value=agent)
    monkeypatch.setattr(commands, "ensure_config", lambda _: config)
    monkeypatch.setattr(commands, "validate_openviking_auth", lambda _: None)
    monkeypatch.setattr(commands, "_init_bot_data", lambda _: None)
    monkeypatch.setattr(commands, "_abort_if_port_in_use", lambda *_: None)
    monkeypatch.setattr(commands, "_redirect_openviking_logs_to_stderr", lambda: None)
    monkeypatch.setattr(commands, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(commands, "logger", Mock())
    monkeypatch.setattr(commands, "CronService", constructor)
    monkeypatch.setattr(commands, "prepare_agent_loop", prepare_agent)
    monkeypatch.setattr(commands, "prepare_channel", lambda *_, **__: channels)
    monkeypatch.setattr(commands, "prepare_agent_channel", lambda *_, **__: channels)
    monkeypatch.setattr(
        commands, "prepare_heartbeat", lambda *_: SimpleNamespace(start=AsyncMock())
    )
    monkeypatch.setattr(
        "vikingbot.compile.service.BotCompileService",
        lambda **_: SimpleNamespace(start=AsyncMock()),
    )
    monkeypatch.setattr("uvicorn.Server", lambda _: SimpleNamespace(serve=AsyncMock()))
    monkeypatch.setenv("VIKINGBOT_LOG_FILE", str(tmp_path / "bot.log"))

    if mode == "gateway":
        commands.gateway(port=None, host="127.0.0.1", verbose=False, config_path=None)
    else:
        commands.chat(
            message=None if mode == "interactive" else "hello",
            session_id="test",
            markdown=False,
            logs=False,
            eval=mode == "eval",
            config_path=None,
            sender=None,
            memory_peer=None,
            memory_user=None,
        )

    should_start = enabled and mode != "eval"
    assert constructor.call_count == int(should_start)
    assert cron.start.await_count == int(should_start)
    assert prepare_agent.call_args.args[3] is (cron if should_start else None)
    agent.close_mcp.assert_awaited_once()
