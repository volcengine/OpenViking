# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for vikingbot chat functionality - single message and interactive modes."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vikingbot.bus.events import OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.channels.chat import ChatChannel, ChatChannelConfig
from vikingbot.channels.single_turn import SingleTurnChannel, SingleTurnChannelConfig
from vikingbot.cli.commands import prepare_agent_channel
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


@pytest.fixture
def config(temp_workspace):
    """Create a mock config."""
    config = MagicMock()
    config.workspace_path = temp_workspace
    config.bot_data_path = temp_workspace / "bot_data"
    config.sandbox = MagicMock()
    config.sandbox.backend = "direct"
    config.sandbox.mode = "user"
    config.agents = MagicMock()
    config.agents.model = "test-model"
    config.agents.api_key = "test-key"
    config.agents.api_base = None
    config.agents.provider = "test-provider"
    config.agents.max_tool_iterations = 10
    config.agents.memory_window = 50
    config.agents.gen_image_model = "test-image-model"
    config.tools = MagicMock()
    config.tools.web = MagicMock()
    config.tools.web.search = MagicMock()
    config.tools.web.search.api_key = None
    config.tools.exec = MagicMock()
    config.hooks = []
    config.heartbeat = MagicMock()
    config.heartbeat.enabled = False
    config.heartbeat.interval_seconds = 60
    config.langfuse = MagicMock()
    config.langfuse.enabled = False
    config.providers = MagicMock()
    return config


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


class TestPrepareAgentChannel:
    """Tests for prepare_agent_channel function."""

    def test_prepare_agent_channel_single_message(self, message_bus, config, temp_workspace):
        """Test prepare_agent_channel with a single message (vikingbot chat -m xxx)."""
        test_message = "Hello, this is a single message"
        session_id = "test-session-123"

        channels = prepare_agent_channel(
            config,
            message_bus,
            message=test_message,
            session_id=session_id,
            markdown=True,
            logs=False,
        )

        assert channels is not None
        # Check that we have a SingleTurnChannel
        assert len(channels.channels) == 1
        # channels is a dict, get the first value
        channel = next(iter(channels.channels.values()))
        assert channel.name == "single_turn"
        assert channel.message == test_message
        assert channel.session_id == session_id

    def test_prepare_agent_channel_interactive(self, message_bus, config, temp_workspace):
        """Test prepare_agent_channel for interactive mode (vikingbot chat)."""
        session_id = "test-session-456"

        channels = prepare_agent_channel(
            config,
            message_bus,
            message=None,  # None means interactive mode
            session_id=session_id,
            markdown=True,
            logs=True,
        )

        assert channels is not None
        # Check that we have a ChatChannel
        assert len(channels.channels) == 1
        # channels is a dict, get the first value
        channel = next(iter(channels.channels.values()))
        assert channel.name == "chat"
        assert channel.session_id == session_id
        assert channel.logs is True


class TestChatCommandCLI:
    """Tests for the chat command CLI interface."""

    def test_vikingbot_chat_help(self):
        """Test that vikingbot chat --help shows correct options."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "vikingbot.cli.commands", "chat", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "--message" in result.stdout or "-m" in result.stdout
        assert "--session" in result.stdout or "-s" in result.stdout
        assert "--markdown" in result.stdout
        assert "--no-markdown" in result.stdout

    def test_vikingbot_gateway_help(self):
        """Test that gateway command help works."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "vikingbot.cli.commands", "gateway", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "--port" in result.stdout or "-p" in result.stdout
