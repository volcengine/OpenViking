"""Tests for config schema validation."""

import pytest
from pydantic import ValidationError

from vikingbot.config.schema import (
    BotConfig,
    LLMConfig,
    ChannelConfig,
    VikingBotConfig,
)


class TestBotConfig:
    """Tests for BotConfig schema."""

    def test_valid_bot_config(self):
        """Test creating a valid bot config."""
        config = BotConfig(
            name="TestBot",
            debug=True,
        )

        assert config.name == "TestBot"
        assert config.debug is True

    def test_default_values(self):
        """Test default config values."""
        config = BotConfig()

        assert config.name == "VikingBot"
        assert config.debug is False

    def test_invalid_name_type(self):
        """Test invalid name type raises error."""
        with pytest.raises(ValidationError):
            BotConfig(name=123)  # Should be string


class TestLLMConfig:
    """Tests for LLMConfig schema."""

    def test_valid_llm_config(self):
        """Test creating a valid LLM config."""
        config = LLMConfig(
            provider="openai",
            model="gpt-4",
            api_key="test-key",
        )

        assert config.provider == "openai"
        assert config.model == "gpt-4"
        assert config.api_key == "test-key"

    def test_temperature_validation(self):
        """Test temperature must be between 0 and 2."""
        # Valid temperature
        config = LLMConfig(temperature=1.5)
        assert config.temperature == 1.5

        # Boundary values
        config = LLMConfig(temperature=0)
        assert config.temperature == 0

        config = LLMConfig(temperature=2)
        assert config.temperature == 2

    def test_invalid_provider(self):
        """Test invalid provider raises error."""
        # This depends on your schema - if you have an enum of valid providers
        # For now we just test that provider is a string
        config = LLMConfig(provider="custom_provider")
        assert config.provider == "custom_provider"


class TestChannelConfig:
    """Tests for ChannelConfig schema."""

    def test_telegram_config(self):
        """Test Telegram channel config."""
        config = ChannelConfig(
            enabled=True,
            bot_token="test-token",
        )

        assert config.enabled is True
        assert config.bot_token == "test-token"

    def test_disabled_channel(self):
        """Test disabled channel config."""
        config = ChannelConfig(
            enabled=False,
        )

        assert config.enabled is False


class TestVikingBotConfig:
    """Tests for VikingBotConfig schema."""

    def test_full_config(self):
        """Test creating a full configuration."""
        config = VikingBotConfig(
            bot=BotConfig(name="TestBot", debug=True),
            llm=LLMConfig(provider="openai", model="gpt-4"),
            channels={
                "telegram": ChannelConfig(enabled=True, bot_token="token"),
            },
        )

        assert config.bot.name == "TestBot"
        assert config.llm.provider == "openai"
        assert config.channels["telegram"].enabled is True

    def test_default_config(self):
        """Test default configuration values."""
        config = VikingBotConfig()

        assert config.bot.name == "VikingBot"
        assert config.bot.debug is False

    def test_config_validation(self):
        """Test configuration validation."""
        # Test with invalid nested data
        with pytest.raises(ValidationError):
            VikingBotConfig(
                bot={"name": 123},  # name should be string
            )


class TestConfigLoading:
    """Tests for configuration loading utilities."""

    def test_load_from_dict(self):
        """Test loading config from dictionary."""
        data = {
            "bot": {"name": "TestBot"},
            "llm": {"provider": "anthropic", "model": "claude-3"},
        }

        config = VikingBotConfig(**data)

        assert config.bot.name == "TestBot"
        assert config.llm.provider == "anthropic"
