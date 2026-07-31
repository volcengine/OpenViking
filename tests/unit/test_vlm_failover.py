# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VLM failover/backup configuration functionality."""

import time
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.models.vlm.base import FailoverVLM, PrimaryBackupSwitcher
from openviking_cli.utils.config.vlm_config import VLMConfig


class TestVLMBackupConfig:
    """Tests for VLMConfig backup field validation and migration."""

    def test_backup_config_migrated_to_credentials(self):
        """Test that backup configuration is migrated to credentials list."""
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
        # After migration, backup should be None but credentials should have 2 entries
        assert config.backup is None
        assert len(config.credentials) == 2
        assert config.credentials[0].id == "legacy-primary"
        assert config.credentials[0].provider == "volcengine"
        assert config.credentials[0].api_key == "primary-key"
        assert config.credentials[0].model == "primary-model"
        assert config.credentials[1].id == "legacy-backup"
        assert config.credentials[1].provider == "openai"
        assert config.credentials[1].api_key == "backup-key"
        assert config.credentials[1].model == "backup-model"

    def test_recursive_backup_config_not_possible(self):
        """Test that recursive backup configurations are automatically prevented by migration.

        With the new multi-credential architecture, when a config with backup is created,
        it gets migrated to credentials format and backup is set to None. This means
        recursive backups are automatically prevented.
        """
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
        # After migration, backup_config.backup should be None
        assert backup_config.backup is None
        # And it should have 2 credentials (primary + backup)
        assert len(backup_config.credentials) == 2

        # Now creating a config with backup=backup_config works (no recursion)
        # because backup_config.backup is already None
        config = VLMConfig(
            model="primary-model",
            api_key="primary-key",
            provider="volcengine",
            backup=backup_config,
        )
        # The outer config gets its own 2 credentials (primary + backup_config's top-level)
        assert len(config.credentials) == 2
        assert config.backup is None

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
        # Should not raise and should migrate to credentials
        assert len(config.credentials) == 2

    def test_per_credential_model_overrides_parent_model(self):
        """Each credential's `model` field should override parent VLMConfig.model
        when building the per-credential VLM config dict."""
        from openviking_cli.utils.config.vlm_config import VLMCredential

        config = VLMConfig(
            model="parent-model",
            credentials=[
                VLMCredential(
                    id="cred-a",
                    provider="volcengine",
                    model="endpoint-a",
                    api_key="key-a",
                    api_base="https://example.com/a",
                ),
                VLMCredential(
                    id="cred-b",
                    provider="volcengine",
                    api_key="key-b",
                    api_base="https://example.com/b",
                ),
            ],
        )

        dict_a = config._build_vlm_config_dict_for_credential(config.credentials[0])
        dict_b = config._build_vlm_config_dict_for_credential(config.credentials[1])

        assert dict_a["model"] == "endpoint-a"
        # Falls back to parent model when credential.model is not set.
        assert dict_b["model"] == "parent-model"


class TestLegacyProvidersDictMigration:
    """Tests for migrating legacy ``providers: {name: {...}}`` configs into credentials.

    Regression coverage for: when only ``providers`` was set (without top-level
    ``provider``/``api_key``), ``_normalize_credentials`` previously produced a
    credential with provider=None and api_key=None, which made ``is_available()``
    return False even though the config was valid in the legacy code path.
    """

    def test_providers_only_single_provider_migrates_provider_and_api_key(self):
        """``providers={openai: {api_key: sk}}`` should yield a usable default credential."""
        cfg = VLMConfig(
            model="gpt-4o-mini",
            providers={"openai": {"api_key": "sk-test", "api_base": "https://api.example.com"}},
        )

        assert len(cfg.credentials) == 1
        cred = cfg.credentials[0]
        assert cred.id == "default"
        assert cred.provider == "openai"
        assert cred.api_key == "sk-test"
        assert cred.api_base == "https://api.example.com"
        assert cred.model == "gpt-4o-mini"

        # is_available() must return True - this was the original blocking bug
        assert cfg.is_available() is True

    def test_providers_only_default_provider_picks_correct_one(self):
        """When ``default_provider`` is set, the matching providers entry is used."""
        cfg = VLMConfig(
            model="gpt-4o-mini",
            default_provider="openai",
            providers={
                "openai": {"api_key": "sk-openai"},
                "azure": {"api_key": "sk-azure", "api_base": "https://azure.example.com"},
            },
        )

        assert len(cfg.credentials) == 1
        cred = cfg.credentials[0]
        assert cred.provider == "openai"
        assert cred.api_key == "sk-openai"
        assert cfg.is_available() is True

    def test_top_level_provider_with_providers_dict_merges_correctly(self):
        """``provider=openai`` plus ``providers={openai: {api_key: ...}}`` works."""
        cfg = VLMConfig(
            model="gpt-4o",
            provider="openai",
            providers={"openai": {"api_key": "sk-merged"}},
        )

        assert len(cfg.credentials) == 1
        cred = cfg.credentials[0]
        assert cred.provider == "openai"
        assert cred.api_key == "sk-merged"
        assert cfg.is_available() is True

    def test_legacy_top_level_api_key_still_works(self):
        """Pure top-level legacy config (provider + api_key) keeps working."""
        cfg = VLMConfig(
            model="gpt-4o",
            provider="openai",
            api_key="sk-top-level",
        )

        assert len(cfg.credentials) == 1
        cred = cfg.credentials[0]
        assert cred.provider == "openai"
        assert cred.api_key == "sk-top-level"
        assert cfg.is_available() is True

    def test_providers_only_propagates_extra_fields(self):
        """extra_headers / extra_request_body / api_version / stream all propagate."""
        cfg = VLMConfig(
            model="gpt-4o",
            providers={
                "openai": {
                    "api_key": "sk",
                    "api_base": "https://api.example.com",
                    "api_version": "2024-01-01",
                    "extra_headers": {"X-Trace": "1"},
                    "extra_request_body": {"foo": "bar"},
                    "stream": True,
                }
            },
        )

        cred = cfg.credentials[0]
        assert cred.provider == "openai"
        assert cred.api_key == "sk"
        assert cred.api_base == "https://api.example.com"
        assert cred.api_version == "2024-01-01"
        assert cred.extra_headers == {"X-Trace": "1"}
        assert cred.extra_request_body == {"foo": "bar"}
        assert cred.stream is True

    def test_explicit_credentials_take_precedence_over_legacy(self):
        """When ``credentials`` is set, legacy providers dict is not migrated again."""
        cfg = VLMConfig(
            model="gpt-4o",
            credentials=[
                {"id": "explicit", "provider": "openai", "api_key": "sk-explicit"},
            ],
            providers={"openai": {"api_key": "sk-legacy"}},
        )

        assert len(cfg.credentials) == 1
        assert cfg.credentials[0].id == "explicit"
        assert cfg.credentials[0].api_key == "sk-explicit"

    def test_legacy_backup_with_primary_providers_dict_resolves_via_match_provider(self):
        """Primary side: ``providers={openai: {...}}`` + backup must yield a usable legacy-primary.

        Regression: previously, backup migration only read top-level
        ``self.provider/self.api_key`` and produced a credential with
        provider=None / api_key=None when the primary used the providers dict.
        """
        cfg = VLMConfig(
            model="gpt-4o",
            providers={"openai": {"api_key": "sk-primary"}},
            backup=VLMConfig(model="gpt-4o-mini", provider="openai", api_key="sk-backup"),
        )

        assert len(cfg.credentials) == 2
        primary = cfg.credentials[0]
        assert primary.id == "legacy-primary"
        assert primary.provider == "openai"
        assert primary.api_key == "sk-primary"
        assert primary.model == "gpt-4o"

        backup = cfg.credentials[1]
        assert backup.id == "legacy-backup"
        assert backup.provider == "openai"
        assert backup.api_key == "sk-backup"
        assert backup.model == "gpt-4o-mini"

        # is_available() must return True
        assert cfg.is_available() is True

    def test_legacy_backup_with_backup_providers_dict_resolves_via_match_provider(self):
        """Backup side: backup using ``providers={...}`` must produce usable legacy-backup."""
        cfg = VLMConfig(
            model="gpt-4o",
            provider="openai",
            api_key="sk-primary",
            backup=VLMConfig(
                model="gpt-4o-mini",
                providers={"openai": {"api_key": "sk-backup"}},
            ),
        )

        assert len(cfg.credentials) == 2
        primary = cfg.credentials[0]
        assert primary.provider == "openai"
        assert primary.api_key == "sk-primary"

        backup = cfg.credentials[1]
        assert backup.id == "legacy-backup"
        assert backup.provider == "openai"
        assert backup.api_key == "sk-backup"

        assert cfg.is_available() is True

    def test_legacy_backup_with_default_provider_resolves(self):
        """Backup using ``default_provider`` to disambiguate is migrated correctly."""
        cfg = VLMConfig(
            model="gpt-4o",
            providers={"openai": {"api_key": "sk-primary"}},
            backup=VLMConfig(
                model="gpt-4o-mini",
                default_provider="azure",
                providers={
                    "openai": {"api_key": "sk-bk-openai"},
                    "azure": {
                        "api_key": "sk-bk-azure",
                        "api_base": "https://azure.example.com",
                        "api_version": "2024-01-01",
                    },
                },
            ),
        )

        assert len(cfg.credentials) == 2
        backup = cfg.credentials[1]
        assert backup.provider == "azure"
        assert backup.api_key == "sk-bk-azure"
        assert backup.api_base == "https://azure.example.com"
        assert backup.api_version == "2024-01-01"

        assert cfg.is_available() is True

    def test_legacy_backup_propagates_extra_fields_via_match_provider(self):
        """Backup migration via providers dict propagates extra_headers / stream / etc."""
        cfg = VLMConfig(
            model="gpt-4o",
            providers={
                "openai": {
                    "api_key": "sk-primary",
                    "extra_headers": {"X-Trace": "p"},
                    "stream": True,
                }
            },
            backup=VLMConfig(
                model="gpt-4o-mini",
                providers={
                    "openai": {
                        "api_key": "sk-backup",
                        "extra_headers": {"X-Trace": "b"},
                        "extra_request_body": {"foo": "bar"},
                        "stream": False,
                    }
                },
            ),
        )

        primary = cfg.credentials[0]
        assert primary.api_key == "sk-primary"
        assert primary.extra_headers == {"X-Trace": "p"}
        assert primary.stream is True

        backup = cfg.credentials[1]
        assert backup.api_key == "sk-backup"
        assert backup.extra_headers == {"X-Trace": "b"}
        assert backup.extra_request_body == {"foo": "bar"}
        assert backup.stream is False


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

    def test_primary_fails_rate_limit_does_not_failover(self):
        """Test that rate limit errors do not trigger failover to backup."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception("rate limit exceeded (429)")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup)

        with pytest.raises(Exception, match="rate limit exceeded"):
            failover.get_completion(prompt="test")

        primary.get_completion.assert_called_once()
        backup.get_completion.assert_not_called()
        assert failover.is_using_backup is False

    def test_primary_fails_quota_exceeded_fails_to_backup(self):
        """Test that AccountQuotaExceeded triggers immediate failover."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception(
            'API Error: 429 {"error":{"code":"AccountQuotaExceeded",'
            '"message":"You have exceeded the 5-hour usage quota. '
            'It will reset at 2026-05-14 17:18:52 +0800 CST."}}'
        )

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

    def test_primary_fails_timeout_does_not_failover(self):
        """Test that timeout errors do not trigger failover to backup."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception("request timeout")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup)

        with pytest.raises(Exception, match="request timeout"):
            failover.get_completion(prompt="test")

        assert failover.is_using_backup is False

    def test_primary_fails_server_error_does_not_failover(self):
        """Test that server errors do not trigger failover to backup."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception("server error 503")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup)

        with pytest.raises(Exception, match="server error 503"):
            failover.get_completion(prompt="test")

        assert failover.is_using_backup is False

    def test_both_fail_raises_last_error(self):
        """Test that if both primary and backup fail, the last error is raised."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = Exception(
            'API Error: 429 {"error":{"code":"AccountQuotaExceeded"}}'
        )

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
        primary.get_completion.side_effect = Exception(
            'API Error: 429 {"error":{"code":"AccountQuotaExceeded"}}'
        )

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
        primary.get_vision_completion.side_effect = Exception(
            'API Error: 429 {"error":{"code":"AccountQuotaExceeded"}}'
        )

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
            side_effect=Exception('API Error: 429 {"error":{"code":"AccountQuotaExceeded"}}')
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
            side_effect=Exception('API Error: 429 {"error":{"code":"AccountQuotaExceeded"}}')
        )

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_vision_completion_async = AsyncMock(return_value="backup async vision response")

        failover = FailoverVLM(primary, backup)

        result = await failover.get_vision_completion_async(prompt="describe", images=["test.jpg"])

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

    def test_config_with_backup_creates_multicredential_instance(self, monkeypatch):
        """Test that config with backup creates a MultiCredentialVLM instance."""
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

        # Should be a MultiCredentialVLM instance
        assert hasattr(instance, "_vlm_instances")
        assert len(instance._vlm_instances) == 2


class TestPrimaryBackupSwitcher:
    """Tests for PrimaryBackupSwitcher."""

    def test_initial_state(self):
        """Test initial state is using primary."""
        switcher = PrimaryBackupSwitcher()
        assert switcher.is_using_backup is False
        assert switcher.should_try_primary() is True

    def test_record_primary_success_no_change(self):
        """Test successful primary calls keep us on primary."""
        switcher = PrimaryBackupSwitcher()
        switcher.record_primary_success()
        assert switcher.is_using_backup is False

    def test_switch_to_backup_on_permanent_error(self):
        """Test permanent error triggers switch to backup."""
        switcher = PrimaryBackupSwitcher()
        error = Exception("403 forbidden")
        switched = switcher.record_primary_failure(error)
        assert switched is True
        assert switcher.is_using_backup is True

    def test_switch_to_backup_on_quota_error(self):
        """Test quota error triggers switch to backup."""
        switcher = PrimaryBackupSwitcher()
        error = Exception("quota exceeded")
        switched = switcher.record_primary_failure(error)
        assert switched is True
        assert switcher.is_using_backup is True

    def test_no_switch_on_other_errors(self):
        """Test other errors don't trigger switch."""
        switcher = PrimaryBackupSwitcher()
        error = Exception("invalid prompt")
        switched = switcher.record_primary_failure(error)
        assert switched is False
        assert switcher.is_using_backup is False

    def test_should_try_primary_after_request_count(self):
        """Test failback attempt after enough backup requests."""
        switcher = PrimaryBackupSwitcher(failback_request_count=3)

        # Switch to backup
        error = Exception("quota exceeded")
        switcher.record_primary_failure(error)
        assert switcher.is_using_backup is True

        # Record backup requests
        assert switcher.should_try_primary() is False
        switcher.record_backup_request()
        assert switcher.should_try_primary() is False
        switcher.record_backup_request()
        assert switcher.should_try_primary() is False
        switcher.record_backup_request()
        assert switcher.should_try_primary() is True

    def test_should_try_primary_after_timeout(self):
        """Test failback attempt after timeout."""
        switcher = PrimaryBackupSwitcher(failback_timeout_seconds=0.1)

        # Switch to backup
        error = Exception("quota exceeded")
        switcher.record_primary_failure(error)
        assert switcher.is_using_backup is True

        # Before timeout
        assert switcher.should_try_primary() is False

        # After timeout
        time.sleep(0.15)
        assert switcher.should_try_primary() is True

    def test_failback_success_stays_on_primary(self):
        """Test successful failback keeps us on primary."""
        switcher = PrimaryBackupSwitcher(failback_request_count=1)

        # Switch to backup
        error = Exception("quota exceeded")
        switcher.record_primary_failure(error)
        switcher.record_backup_request()

        # Now try primary and succeed
        assert switcher.should_try_primary() is True
        switcher.record_primary_success()

        assert switcher.is_using_backup is False

    def test_failback_failure_goes_back_to_backup(self):
        """Test failed failback goes back to backup and resets counter."""
        switcher = PrimaryBackupSwitcher(failback_request_count=2)

        # Switch to backup
        error = Exception("quota exceeded")
        switcher.record_primary_failure(error)
        switcher.record_backup_request()
        switcher.record_backup_request()

        # Now try primary and fail again
        assert switcher.should_try_primary() is True
        switched = switcher.record_primary_failure(Exception("quota exceeded again"))

        assert switched is True
        assert switcher.is_using_backup is True

        # Counter should be reset
        switcher.record_backup_request()
        assert switcher.should_try_primary() is False


class TestFailoverVLMAutomaticFailback:
    """Tests for FailoverVLM automatic failback functionality."""

    def test_failback_after_request_count(self):
        """Test automatic failback after enough backup requests."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        # First call fails, then succeeds
        primary.get_completion.side_effect = [
            Exception("quota exceeded"),
            "primary is back!",
        ]

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup, failback_request_count=2)

        # First call - fails over to backup
        result1 = failover.get_completion(prompt="test1")
        assert result1 == "backup response"
        assert failover.is_using_backup is True

        # Second call - still using backup
        result2 = failover.get_completion(prompt="test2")
        assert result2 == "backup response"

        # Third call - should try primary again and succeed
        result3 = failover.get_completion(prompt="test3")
        assert result3 == "primary is back!"
        assert failover.is_using_backup is False

    def test_failback_failure_stays_on_backup(self):
        """Test if failback fails, it stays on backup and resets counter."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        # Always fails
        primary.get_completion.side_effect = Exception("quota exceeded")

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup, failback_request_count=2)

        # First call - fails over
        result1 = failover.get_completion(prompt="test1")
        assert result1 == "backup response"

        # Second call - still on backup
        result2 = failover.get_completion(prompt="test2")
        assert result2 == "backup response"

        # Third call - tries primary, fails again, goes back to backup
        result3 = failover.get_completion(prompt="test3")
        assert result3 == "backup response"
        assert failover.is_using_backup is True

        # Counter should be reset - next call doesn't try primary yet
        result4 = failover.get_completion(prompt="test4")
        assert result4 == "backup response"

    def test_failback_after_timeout(self):
        """Test automatic failback after timeout."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion.side_effect = [
            Exception("quota exceeded"),
            "primary is back!",
        ]

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion.return_value = "backup response"

        failover = FailoverVLM(primary, backup, failback_timeout_seconds=0.1)

        # First call - fails over
        result1 = failover.get_completion(prompt="test1")
        assert result1 == "backup response"
        assert failover.is_using_backup is True

        # Wait for timeout
        time.sleep(0.15)

        # Next call - should try primary again
        result2 = failover.get_completion(prompt="test2")
        assert result2 == "primary is back!"
        assert failover.is_using_backup is False

    @pytest.mark.asyncio
    async def test_async_failback_after_request_count(self):
        """Test automatic failback works with async methods."""
        primary = Mock()
        primary.model = "primary-model"
        primary.provider = "volcengine"
        primary.get_completion_async = AsyncMock(
            side_effect=[
                Exception("quota exceeded"),
                "primary async is back!",
            ]
        )

        backup = Mock()
        backup.model = "backup-model"
        backup.provider = "openai"
        backup.get_completion_async = AsyncMock(return_value="backup async response")

        failover = FailoverVLM(primary, backup, failback_request_count=2)

        # First call - fails over
        result1 = await failover.get_completion_async(prompt="test1")
        assert result1 == "backup async response"

        # Second call - still on backup
        result2 = await failover.get_completion_async(prompt="test2")
        assert result2 == "backup async response"

        # Third call - tries primary and succeeds
        result3 = await failover.get_completion_async(prompt="test3")
        assert result3 == "primary async is back!"
        assert failover.is_using_backup is False
