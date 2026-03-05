# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for vikingbot chat commands"""

import subprocess
import sys
from pathlib import Path

import pytest


def test_vikingbot_chat_help():
    """Test that vikingbot chat --help shows correct options"""
    # Run vikingbot chat --help
    result = subprocess.run(
        [sys.executable, "-m", "vikingbot.cli.commands", "chat", "--help"],
        capture_output=True,
        text=True,
    )

    # Check exit code
    assert result.returncode == 0, f"Command failed: {result.stderr}"

    # Check that expected options are present
    assert "--message" in result.stdout or "-m" in result.stdout
    assert "--session" in result.stdout or "-s" in result.stdout
    assert "--markdown" in result.stdout
    assert "--no-markdown" in result.stdout
    assert "--logs" in result.stdout
    assert "--no-logs" in result.stdout


def test_vikingbot_command_exists():
    """Test that vikingbot command can be invoked"""
    # Just check that the main module can be imported and shows help
    result = subprocess.run(
        [sys.executable, "-m", "vikingbot.cli.commands", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "chat" in result.stdout
    assert "Interact with the agent directly" in result.stdout


def test_vikingbot_gateway_help():
    """Test that gateway command help works"""
    result = subprocess.run(
        [sys.executable, "-m", "vikingbot.cli.commands", "gateway", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--port" in result.stdout or "-p" in result.stdout


def test_single_turn_channel_import():
    """Test that SingleTurnChannel can be imported"""
    from vikingbot.channels.single_turn import SingleTurnChannel, SingleTurnChannelConfig

    assert SingleTurnChannel is not None
    assert SingleTurnChannelConfig is not None


def test_chat_channel_import():
    """Test that ChatChannel can be imported"""
    from vikingbot.channels.chat import ChatChannel, ChatChannelConfig

    assert ChatChannel is not None
    assert ChatChannelConfig is not None


def test_prepare_agent_channel_function():
    """Test that prepare_agent_channel function exists and can be imported"""
    from vikingbot.cli.commands import prepare_agent_channel

    assert prepare_agent_channel is not None
    assert callable(prepare_agent_channel)


def test_chat_command_function_exists():
    """Test that the chat command function is registered"""
    from vikingbot.cli.commands import app

    # Check that we can get the chat command info
    # Typer stores commands differently, let's just verify the chat attribute exists
    assert hasattr(app, "commands") or hasattr(app, "registered_commands")

    # Try calling the chat command with --help as a better verification
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "vikingbot.cli.commands", "chat", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Interact with the agent directly" in result.stdout


@pytest.mark.integration
def test_chat_single_turn_dry_run():
    """
    Dry run test for single-turn chat (doesn't actually call LLM)
    This tests the infrastructure without requiring API keys
    """
    # This test just verifies the modules load correctly
    # A full integration test would need API keys and a running agent
    from vikingbot.channels.single_turn import SingleTurnChannel, SingleTurnChannelConfig
    from vikingbot.bus.queue import MessageBus

    config = SingleTurnChannelConfig()
    bus = MessageBus()

    # Just verify we can instantiate the channel without errors
    channel = SingleTurnChannel(
        config,
        bus,
        workspace_path=Path("/tmp"),
        message="Hello, test",
        session_id="test-session",
        markdown=True,
    )

    assert channel is not None
    assert channel.name == "single_turn"
    assert channel.message == "Hello, test"
    assert channel.session_id == "test-session"

