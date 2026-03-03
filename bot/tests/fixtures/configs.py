"""Config fixtures for testing."""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ConfigFixture:
    """Configuration fixture data class."""

    name: str
    config: dict
    valid: bool = True
    expected_error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "config": self.config,
            "valid": self.valid,
            "expected_error": self.expected_error,
        }


# Minimal valid configuration
MINIMAL_CONFIG = ConfigFixture(
    name="minimal_valid",
    config={
        "bot": {"name": "TestBot"},
        "llm": {
            "provider": "openai",
            "model": "gpt-4",
            "api_key": "test-key",
        },
    },
)

# Full configuration with all options
FULL_CONFIG = ConfigFixture(
    name="full_config",
    config={
        "bot": {
            "name": "FullTestBot",
            "debug": True,
            "log_level": "DEBUG",
        },
        "llm": {
            "provider": "openai",
            "model": "gpt-4-turbo-preview",
            "api_key": "sk-test-key",
            "temperature": 0.7,
            "max_tokens": 2000,
            "timeout": 60,
        },
        "memory": {
            "enabled": True,
            "max_history": 50,
            "consolidation_threshold": 40,
        },
        "channels": {
            "telegram": {
                "enabled": True,
                "bot_token": "test-telegram-token",
                "webhook_url": "https://example.com/telegram/webhook",
            },
            "feishu": {
                "enabled": True,
                "app_id": "test-app-id",
                "app_secret": "test-app-secret",
                "encrypt_key": "test-encrypt-key",
                "verification_token": "test-verification-token",
                "webhook_url": "https://example.com/feishu/webhook",
            },
            "discord": {
                "enabled": False,
                "bot_token": "test-discord-token",
            },
        },
        "sandbox": {
            "enabled": True,
            "max_execution_time": 30,
            "allowed_commands": ["python", "bash"],
        },
    },
)

# Invalid configurations
INVALID_NO_LLM = ConfigFixture(
    name="invalid_no_llm",
    config={
        "bot": {"name": "TestBot"},
    },
    valid=False,
    expected_error="llm",
)

INVALID_BAD_PROVIDER = ConfigFixture(
    name="invalid_bad_provider",
    config={
        "bot": {"name": "TestBot"},
        "llm": {
            "provider": "invalid_provider",
            "api_key": "test",
        },
    },
    valid=False,
    expected_error="provider",
)

INVALID_TEMPERATURE_HIGH = ConfigFixture(
    name="invalid_temperature_high",
    config={
        "bot": {"name": "TestBot"},
        "llm": {
            "provider": "openai",
            "api_key": "test",
            "temperature": 5.0,  # Invalid: should be 0-2
        },
    },
    valid=False,
    expected_error="temperature",
)


def get_all_fixtures() -> list[ConfigFixture]:
    """Get all config fixtures."""
    return [
        MINIMAL_CONFIG,
        FULL_CONFIG,
        INVALID_NO_LLM,
        INVALID_BAD_PROVIDER,
        INVALID_TEMPERATURE_HIGH,
    ]


def get_valid_fixtures() -> list[ConfigFixture]:
    """Get only valid config fixtures."""
    return [f for f in get_all_fixtures() if f.valid]


def get_invalid_fixtures() -> list[ConfigFixture]:
    """Get only invalid config fixtures."""
    return [f for f in get_all_fixtures() if not f.valid]


def get_fixture_by_name(name: str) -> ConfigFixture | None:
    """Get a fixture by its name."""
    for fixture in get_all_fixtures():
        if fixture.name == name:
            return fixture
    return None
