"""
Pytest configuration and shared fixtures for vikingbot tests.

This module provides shared fixtures and configuration for all tests.
Fixtures here should be generic and reusable across test modules.
"""

import sys
from pathlib import Path
from datetime import datetime

import pytest

# Add the bot directory to path so we can import vikingbot
bot_path = Path(__file__).parent / "../.."
sys.path.insert(0, str(bot_path.resolve()))


@pytest.fixture
def sample_session_key():
    """
    Fixture: Provide a sample SessionKey for testing.

    Purpose: Create a consistent SessionKey for tests that need one.
    Spec: Returns a SessionKey with type="test", channel_id="test_channel", chat_id="test_chat".
    """
    from vikingbot.config.schema import SessionKey

    return SessionKey(type="test", channel_id="test_channel", chat_id="test_chat")


@pytest.fixture
def another_session_key():
    """
    Fixture: Provide another distinct SessionKey for testing.

    Purpose: Create a different SessionKey for tests comparing multiple sessions.
    Spec: Returns a SessionKey with type="test", channel_id="test_channel", chat_id="another_chat".
    """
    from vikingbot.config.schema import SessionKey

    return SessionKey(type="test", channel_id="test_channel", chat_id="another_chat")


@pytest.fixture
def fixed_datetime():
    """
    Fixture: Provide a fixed datetime for testing.

    Purpose: Create a consistent timestamp for tests that need predictable time values.
    Spec: Returns datetime(2024, 1, 1, 12, 0, 0).
    """
    return datetime(2024, 1, 1, 12, 0, 0)


@pytest.fixture
def temp_dir(tmp_path):
    """
    Fixture: Provide a temporary directory.

    Purpose: Create a temporary directory for tests that need file system operations.
    Spec: Returns a Path object pointing to a temporary directory.
    """
    return tmp_path
