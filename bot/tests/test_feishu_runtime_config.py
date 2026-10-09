"""Feishu runtime configuration must not block the event loop."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.bus.queue import MessageBus
from vikingbot.channels import feishu
from vikingbot.channels.feishu import FeishuChannel
from vikingbot.config.schema import Config, FeishuChannelConfig


def _channel(*, bot_config: Config | None = None) -> FeishuChannel:
    return FeishuChannel(
        FeishuChannelConfig(
            app_id="cli_test",
            thread_require_mention=False,
        ),
        MessageBus(),
        bot_config=bot_config,
    )


@pytest.mark.asyncio
async def test_runtime_config_uses_injected_config_without_reload(monkeypatch):
    config = Config()
    channel = _channel(bot_config=config)

    def unexpected_load():
        raise AssertionError("Feishu must reuse its injected Bot config")

    monkeypatch.setattr(feishu, "load_config", unexpected_load)

    assert await channel._get_bot_config() is config


@pytest.mark.asyncio
async def test_runtime_config_offloads_and_caches_fallback_load(monkeypatch):
    config = Config()
    channel = _channel()
    event_loop_thread = threading.get_ident()
    load_threads = []

    def load():
        load_threads.append(threading.get_ident())
        return config

    monkeypatch.setattr(feishu, "load_config", load)

    assert await channel._get_bot_config() is config
    assert await channel._get_bot_config() is config
    assert len(load_threads) == 1
    assert load_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_thread_policy_uses_runtime_config_without_reload(monkeypatch):
    channel = _channel(bot_config=Config())
    channel._get_chat_mode = AsyncMock(return_value="thread")

    def unexpected_load():
        raise AssertionError("thread policy must not reload Bot config")

    monkeypatch.setattr(feishu, "load_config", unexpected_load)

    should_process = await channel._check_should_process(
        "group",
        "oc_test",
        SimpleNamespace(root_id="", message_id="om_test"),
        False,
    )

    assert should_process is True


@pytest.mark.asyncio
async def test_inbound_reaction_uses_runtime_config_without_reload(monkeypatch):
    channel = _channel(bot_config=Config())
    channel._parse_message_content = AsyncMock(return_value=("hello", []))
    channel._check_should_process = AsyncMock(return_value=True)
    channel._add_reaction = AsyncMock(return_value=None)
    channel._handle_message = AsyncMock(return_value=None)

    def unexpected_load():
        raise AssertionError("inbound reaction must not reload Bot config")

    monkeypatch.setattr(feishu, "load_config", unexpected_load)
    data = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id="om_test",
                chat_id="ou_test",
                chat_type="p2p",
                message_type="text",
                content='{"text":"hello"}',
                mentions=[],
                root_id="",
            ),
            sender=SimpleNamespace(
                sender_type="user",
                sender_id=SimpleNamespace(open_id="ou_test"),
            ),
        )
    )

    await channel._on_message(data)

    channel._add_reaction.assert_awaited_once_with("om_test", "MeMeMe")
    channel._handle_message.assert_awaited_once()
