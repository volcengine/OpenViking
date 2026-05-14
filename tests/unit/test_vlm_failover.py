# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VLM failover/backup configuration functionality."""

import pytest
from unittest.mock import Mock, AsyncMock

from openviking.models.vlm.base import FailoverVLM, VLMResponse
from openviking_cli.utils.config.vlm_config import VLMConfig


class TestVLMBackupConfig:
    """Tests for VLMConfig backup field validation."""

    def test_backup_config_allowed(self):
        """Test that backup configuration is allowed when not recursive."""
        backup_config = VLMConfig(
            model="backup-model",
            api_key="backup-key",
            provider="openai",
        )
        config = VLMConfig(
            model="primary-model",
            api_key="primary-key",
            provider="volcengine",
            backup=backup_config,
        )
        assert config.backup is not None
        assert config.backup.model == "backup-model"

    def test_recursive_backup_config_rejected(self):
        """Test that recursive backup configurations are rejected."""
        nested_backup = VLMConfig(
            model="nested-model",
            api_key="nested-key",
            provider="openai",
        )
        backup_config = VLMConfig(
            model="backup-model",
            api_key="backup-key",
            provider="openai",
            backup=nested_backup,
        )
        with pytest.raises(ValueError, match="recursive backups are not allowed"):
            VLMConfig(
                model="primary-model",
                api_key="primary-key",
                provider="volcengine",
                backup=backup_config,
            )

    def test_backup_without_own_backup_allowed(self):
        """Test that backup config without its own backup is allowed."""
        backup_config = VLMConfig(
            model="backup-model",
            api_key="backup-key",
            provider="openai",
        )
        config = VLMConfig(
            model="primary-model",
            api_key="primary-key",
            provider="volcengine",
            backup=backup_config,
        )
        # Should not raise
        config.validate_no_recursive_backup()


class TestFailoverVLM:
    """Tests for FailoverVLM wrapper."""

    def test_initialization(self):
        """Test that FailoverVLM initializes correctly with primary and backup."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"

        failover = FailoverVLM(primary, backup)

        assert failover.primary is primary
        assert failover.backup is backup
        assert failover.is_using_backup is False

    def test_primary_success(self):
        """Test that primary is used when it succeeds."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.return_value = "primary response"

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"

        failover = FailoverVLM(primary, backup)

        result = failover.get_completion(prompt="test")

        assert result == "primary response"
        primary.get_completion.assert_called_once()
        backup.get_completion.assert_not_called()
        assert failover.is_using_backup is False

    def test_primary_fails_non_retryable(self):
        """Test that non-retryable errors don't trigger failover."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = ValueError("invalid prompt")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"

        failover = FailoverVLM(primary, backup)

        with pytest.raises(ValueError, match="invalid prompt"):
            failover.get_completion(prompt="test")

        primary.get_completion.assert_called_once()
        backup.get_completion.assert_not_called()
        assert failover.is_using_backup is False

    def test_primary_fails_rate_limit_fails_to_backup(self):
        """Test that rate limit errors trigger failover to backup."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception("rate limit exceeded (429)")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup)

        result = failover.get_completion(prompt="test")

        assert result == "backup response"
        primary.get_completion.assert_called_once()
        backup.get_completion.assert_called_once()
        assert failover.is_using_backup is True

    def test_primary_fails_timeout_fails_to_backup(self):
        """Test that timeout errors trigger failover to backup."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception("request timeout")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup)

        result = failover.get_completion(prompt="test")

        assert result == "backup response"
        assert failover.is_using_backup is True

    def test_primary_fails_server_error_fails_to_backup(self):
        """Test that server errors trigger failover to backup."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception("server error 503")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup)

        result = failover.get_completion(prompt="test")

        assert result == "backup response"
        assert failover.is_using_backup is True

    def test_both_fail_raises_last_error(self):
        """Test that if both primary and backup fail, the last error is raised."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception("rate limit exceeded (429)")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.side_effect = Exception("backup also failed")

        failover = FailoverVLM(primary, backup)

        with pytest.raises(Exception, match="backup also failed"):
            failover.get_completion(prompt="test")

        primary.get_completion.assert_called_once()
        backup.get_completion.assert_called_once()

    def test_stays_on_backup_after_switch(self):
        """Test that once switched to backup, subsequent calls use backup."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception("rate limit exceeded (429)")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup)

        # First call triggers failover
        result1 = failover.get_completion(prompt="test1")
        assert result1 == "backup response"
        assert failover.is_using_backup is True

        # Second call should use backup directly, not try primary again
        backup.get_completion.return_value = "backup response 2"
        result2 = failover.get_completion(prompt="test2")
        assert result2 == "backup response 2"
        assert primary.get_completion.call_count == 1  # Only called once

    def test_vision_completion_failover(self):
        """Test failover works for vision completion."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_vision_completion.side_effect = Exception("rate limit exceeded (429)")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_vision_completion.return_value = "backup vision response"

        failover = FailoverVLM(primary, backup)

        result = failover.get_vision_completion(prompt="describe", images=["test.jpg"])

        assert result == "backup vision response"
        primary.get_vision_completion.assert_called_once()
        backup.get_vision_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_completion_failover(self):
        """Test failover works for async completion."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion_async = AsyncMock(
            side_effect=Exception("rate limit exceeded (429)")
        )

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion_async = AsyncMock(return_value="backup async response")

        failover = FailoverVLM(primary, backup)

        result = await failover.get_completion_async(prompt="test")

        assert result == "backup async response"
        primary.get_completion_async.assert_called_once()
        backup.get_completion_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_vision_completion_failover(self):
        """Test failover works for async vision completion."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_vision_completion_async = AsyncMock(
            side_effect=Exception("rate limit exceeded (429)")
        )

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_vision_completion_async = AsyncMock(
            return_value="backup async vision response"
        )

        failover = FailoverVLM(primary, backup)

        result = await failover.get_vision_completion_async(
            prompt="describe", images=["test.jpg"]
        )

        assert result == "backup async vision response"


class TestVLMConfigWithBackup:
    """Tests for VLMConfig with backup configuration integration."""

    def test_config_without_backup_creates_single_instance(self, monkeypatch):
        """Test that config without backup creates a single VLM instance."""
        mock_factory = Mock()
        mock_vlm = Mock()
        mock_factory.create.return_value = mock_vlm

        monkeypatch.setattr("openviking.models.vlm.VLMFactory", mock_factory)

        config = VLMConfig(
            model="test-model",
            api_key="test-key",
            provider="volcengine",
        )

        instance = config.get_vlm_instance()

        assert instance is mock_vlm
        mock_factory.create.assert_called_once()

    def test_config_with_backup_creates_failover_instance(self, monkeypatch):
        """Test that config with backup creates a FailoverVLM instance."""
        mock_factory = Mock()
        mock_primary = Mock()
        mock_backup = Mock()
        mock_factory.create.side_effect = [mock_primary, mock_backup]

        monkeypatch.setattr("openviking.models.vlm.VLMFactory", mock_factory)

        backup_config = VLMConfig(
            model="backup-model",
            api_key="backup-key",
            provider="openai",
        )
        config = VLMConfig(
            model="primary-model",
            api_key="primary-key",
            provider="volcengine",
            backup=backup_config,
        )

        instance = config.get_vlm_instance()

        # Should be a FailoverVLM instance
        assert hasattr(instance, "primary")
        assert hasattr(instance, "backup")
