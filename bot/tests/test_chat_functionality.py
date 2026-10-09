# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI response delivery and provider-compatible conversation history."""

import pytest
from vikingbot.bus.events import OutboundEventType, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.chat import ChatChannel, ChatChannelConfig
from vikingbot.channels.single_turn import SingleTurnChannel, SingleTurnChannelConfig
from vikingbot.config.schema import SessionKey
from vikingbot.session.manager import Session


@pytest.mark.parametrize(
    "channel_cls, config_cls, options",
    [
        pytest.param(
            SingleTurnChannel, SingleTurnChannelConfig, {"message": "hello"}, id="single-turn"
        ),
        pytest.param(ChatChannel, ChatChannelConfig, {"logs": False}, id="interactive"),
    ],
)
async def test_cli_channel_receives_response(tmp_path, channel_cls, config_cls, options):
    channel = channel_cls(
        config_cls(),
        MessageBus(),
        workspace_path=tmp_path,
        session_id="test-session",
        markdown=True,
        **options,
    )
    channel._running = True
    await channel.send(
        OutboundMessage(
            session_key=SessionKey(type="cli", channel_id="default", chat_id="test-session"),
            content="test response",
        )
    )
    assert channel._last_response == "test response"
    assert channel._response_received.is_set()


async def test_interactive_cli_renders_progress_without_finishing_response(tmp_path, monkeypatch):
    printed = []

    class RecordingConsole:
        def print(self, *objects, **_kwargs):
            printed.append(" ".join(str(obj) for obj in objects))

    monkeypatch.setattr("vikingbot.cli.commands.console", RecordingConsole())
    channel = ChatChannel(
        ChatChannelConfig(),
        MessageBus(),
        workspace_path=tmp_path,
        session_id="test-session",
        markdown=True,
        logs=False,
    )

    await channel.send(
        OutboundMessage(
            session_key=SessionKey(type="cli", channel_id="default", chat_id="test-session"),
            content="Compacting session memory...",
            event_type=OutboundEventType.PROGRESS,
        )
    )

    assert printed == ["  [dim]Compacting session memory...[/dim]"]
    assert channel._last_response is None
    assert not channel._response_received.is_set()


@pytest.mark.parametrize("provider", ["deepseek", "openai"])
def test_history_includes_reasoning_only_for_deepseek(provider):
    session = Session(key=SessionKey(type="cli", channel_id="default", chat_id="test-session"))
    session.add_message("user", "hello")
    session.add_message("assistant", "hi", reasoning_content="internal reasoning")
    assistant = {"role": "assistant", "content": "hi"}
    if provider == "deepseek":
        assistant["reasoning_content"] = "internal reasoning"
    assert session.get_history(provider_name=provider) == [
        {"role": "user", "content": "hello"},
        assistant,
    ]
