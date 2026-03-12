# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for vikingbot chat functionality - single message and interactive modes."""

import tempfile
from pathlib import Path

import pytest
from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.chat import ChatChannel, ChatChannelConfig
from vikingbot.channels.single_turn import SingleTurnChannel, SingleTurnChannelConfig
from vikingbot.config.schema import SessionKey


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def message_bus():
    """Create a MessageBus instance."""
    return MessageBus()


class TestSingleTurnChannel:
    """Tests for SingleTurnChannel (vikingbot chat -m xxx)."""

    def test_single_turn_channel_initialization(self, message_bus, temp_workspace):
        """Test that SingleTurnChannel can be initialized correctly."""
        config = SingleTurnChannelConfig()
        channel = SingleTurnChannel(
            config,
            message_bus,
            workspace_path=temp_workspace,
            message="Hello, test",
            session_id="test-session",
            markdown=True,
        )

        assert channel is not None
        assert channel.name == "single_turn"
        assert channel.message == "Hello, test"
        assert channel.session_id == "test-session"

    @pytest.mark.asyncio
    async def test_single_turn_channel_receives_response(self, message_bus, temp_workspace):
        """Test that SingleTurnChannel can receive and store responses."""
        config = SingleTurnChannelConfig()
        test_message = "Hello, test"
        channel = SingleTurnChannel(
            config,
            message_bus,
            workspace_path=temp_workspace,
            message=test_message,
            session_id="test-session",
            markdown=True,
        )

        # Create a test response
        session_key = SessionKey(type="cli", channel_id="default", chat_id="test-session")
        test_response = "This is a test response from the bot"

        # Send the response
        await channel.send(
            OutboundMessage(
                session_key=session_key,
                content=test_response,
            )
        )

        # Check that the response was stored
        assert channel._last_response == test_response
        assert channel._response_received.is_set()


class TestChatChannel:
    """Tests for ChatChannel (interactive vikingbot chat)."""

    def test_chat_channel_initialization(self, message_bus, temp_workspace):
        """Test that ChatChannel can be initialized correctly."""
        config = ChatChannelConfig()
        channel = ChatChannel(
            config,
            message_bus,
            workspace_path=temp_workspace,
            session_id="test-session",
            markdown=True,
            logs=False,
        )

        assert channel is not None
        assert channel.name == "chat"
        assert channel.session_id == "test-session"

    @pytest.mark.asyncio
    async def test_chat_channel_send_response(self, message_bus, temp_workspace):
        """Test that ChatChannel can receive and store responses."""
        config = ChatChannelConfig()
        channel = ChatChannel(
            config,
            message_bus,
            workspace_path=temp_workspace,
            session_id="test-session",
            markdown=True,
            logs=False,
        )

        # Start the channel in background (it will wait for input)
        channel._running = True

        # Create a test response
        session_key = SessionKey(type="cli", channel_id="default", chat_id="test-session")
        test_response = "This is a test response from the bot"

        # Send the response
        await channel.send(
            OutboundMessage(
                session_key=session_key,
                content=test_response,
            )
        )

        # Check that the response was stored
        assert channel._last_response == test_response
        assert channel._response_received.is_set()
