# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cron is opt-in for both tool registration and scheduler startup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.factory import register_default_tools, register_subagent_tools
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.bus.events import InboundMessage, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.cli import commands
from vikingbot.config.schema import Config, SessionKey
from vikingbot.cron.service import CronService
from vikingbot.cron.types import CronSchedule


@pytest.mark.parametrize("enabled", [False, True])
def test_cron_tool_registration(tmp_path, enabled):
    config = Config(tools={"cron": {"enabled": enabled}})
    registry = ToolRegistry(config=config)
    service = CronService(tmp_path / "jobs.json")
    register_default_tools(registry, config, cron_service=service)
    assert registry.has("cron") is enabled
    names = {tool["function"]["name"] for tool in registry.get_definitions()}
    assert ("cron" in names) is enabled

    subagent = ToolRegistry(config=config)
    register_subagent_tools(subagent, config)
    assert not subagent.has("cron")

    without_service = ToolRegistry(config=config)
    register_default_tools(without_service, config)
    assert not without_service.has("cron")


def test_response_completed_payload_counts_model_tool_outcomes():
    session_key = SessionKey(type="cli", channel_id="default", chat_id="cron")
    payload = AgentLoop._build_response_completed_payload(
        msg=InboundMessage(sender_id="user", content="run", session_key=session_key),
        response_id="response-id",
        final_content="done",
        final_reasoning_content=None,
        token_usage={},
        time_cost_seconds=0,
        iteration=2,
        tools_used=[
            {"tool_name": "auto_memory_search", "execute_success": True, "auto": True},
            {"tool_name": "calendar", "execute_success": False},
            {"tool_name": "mail", "execute_success": False},
        ],
    )

    assert payload["tool_success_count"] == 0
    assert payload["tool_failure_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("success_count", "failure_count", "expected_status"),
    [(0, 1, "error"), (0, 2, "error"), (0, 0, "ok"), (1, 2, "ok")],
)
async def test_cron_uses_tool_outcomes_for_run_status(
    tmp_path, monkeypatch, success_count, failure_count, expected_status
):
    config = Config(storage_workspace=str(tmp_path), tools={"cron": {"enabled": True}})
    monkeypatch.setattr(commands, "get_data_dir", lambda: tmp_path)
    bus = MessageBus()
    service = commands.prepare_cron(config, bus, quiet=True)
    assert service is not None

    session_key = SessionKey(type="cli", channel_id="default", chat_id="cron")
    result = OutboundMessage(
        session_key=session_key,
        content="scheduled response",
        tool_success_count=success_count,
        tool_failure_count=failure_count,
    )
    service._agent_holder["agent"] = SimpleNamespace(
        process_direct_detailed=AsyncMock(return_value=result)
    )
    job = service.add_job(
        "scheduled",
        CronSchedule(kind="every", every_ms=60_000),
        "collect data",
        session_key,
        deliver=True,
    )

    assert await service.run_job(job.id)
    delivered = await bus.consume_outbound()
    assert delivered.content == "scheduled response"
    assert job.state.last_status == expected_status
    if expected_status == "error":
        noun = "call" if failure_count == 1 else "calls"
        assert job.state.last_error == f"All {failure_count} attempted tool {noun} failed"
    else:
        assert job.state.last_error is None


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
