"""Global test fixtures and configuration."""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Set test environment variables before importing any application code
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("VIKINGBOT_CONFIG_DIR", tempfile.mkdtemp())


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config() -> dict:
    """Return a sample configuration for testing."""
    return {
        "bot": {
            "name": "TestBot",
            "debug": True,
        },
        "llm": {
            "provider": "openai",
            "model": "gpt-4",
            "api_key": "test-api-key",
        },
        "channels": {
            "telegram": {
                "enabled": False,
                "bot_token": "test-token",
            }
        },
    }


@pytest.fixture
def mock_llm_response() -> str:
    """Return a mock LLM response."""
    return '```json\n{\n  "thought": "This is a test response",\n  "actions": []\n}\n```'


@pytest_asyncio.fixture
async def mock_message_bus() -> AsyncGenerator[MagicMock, None]:
    """Create a mock message bus for testing."""
    mock_bus = MagicMock()
    mock_bus.inbound = MagicMock()
    mock_bus.outbound = MagicMock()
    mock_bus.inbound.put = AsyncMock()
    mock_bus.outbound.put = AsyncMock()
    mock_bus.inbound.get = AsyncMock(return_value=None)
    mock_bus.outbound.get = AsyncMock(return_value=None)
    yield mock_bus


@pytest.fixture
def patch_env_vars(temp_dir: Path) -> Generator[None, None, None]:
    """Patch environment variables for testing."""
    env_vars = {
        "OPENAI_API_KEY": "test-openai-key",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "TELEGRAM_BOT_TOKEN": "test-telegram-token",
        "FEISHU_APP_ID": "test-feishu-app-id",
        "FEISHU_APP_SECRET": "test-feishu-secret",
        "VIKINGBOT_CONFIG_DIR": str(temp_dir),
    }
    with patch.dict(os.environ, env_vars, clear=False):
        yield


@pytest.fixture(scope="function", autouse=True)
def reset_singletons():
    """Reset any singleton instances between tests."""
    # This is a placeholder - add specific singleton reset logic as needed
    yield
    # Cleanup after test


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "slow: Slow running tests")
    config.addinivalue_line("markers", "async_test: Async tests")
