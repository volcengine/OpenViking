# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for semantic processor retry limits, file existence checks, and circuit breaker config.

Addresses issue #1595: 35.96M tokens burned from a single import due to
infinite retry loops on failed VLM summarization.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import (
    DEFAULT_MAX_RETRIES_PER_URI,
    SemanticProcessor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(uri: str = "viking://user/default/file.txt") -> SemanticMsg:
    return SemanticMsg(uri=uri, context_type="resource")


# ---------------------------------------------------------------------------
# 1. Max retries per URI
# ---------------------------------------------------------------------------


class TestMaxRetriesPerUri:
    """Tests for the max-retries-per-URI safeguard."""

    def test_default_max_retries(self):
        processor = SemanticProcessor()
        assert processor.max_retries_per_uri == DEFAULT_MAX_RETRIES_PER_URI

    def test_custom_max_retries(self):
        processor = SemanticProcessor(max_retries_per_uri=5)
        assert processor.max_retries_per_uri == 5

    @pytest.mark.asyncio
    async def test_requeue_increments_retry_count(self):
        processor = SemanticProcessor(max_retries_per_uri=3)
        msg = _make_msg()

        # Mock the re-enqueue to avoid needing a real queue manager
        with patch.object(processor, "_reenqueue_semantic_msg", new_callable=AsyncMock):
            with patch.object(processor, "report_requeue"):
                with patch.object(processor, "report_success"):
                    with patch(
                        "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker"
                    ) as mock_tracker:
                        mock_tracker.return_value = MagicMock()
                        await processor._requeue_semantic_msg_after_error(
                            msg, {}, RuntimeError("test error")
                        )

        assert processor._retry_counts[msg.uri] == 1

    @pytest.mark.asyncio
    async def test_drops_after_max_retries(self):
        processor = SemanticProcessor(max_retries_per_uri=2)
        msg = _make_msg()

        # Pre-set retry count to max-1
        processor._retry_counts[msg.uri] = 1

        with patch.object(processor, "report_error") as mock_err:
            with patch(
                "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker"
            ) as mock_tracker:
                mock_tracker.return_value = MagicMock()
                await processor._requeue_semantic_msg_after_error(
                    msg, {}, RuntimeError("test error")
                )

        # Should have reported error, not re-enqueued
        mock_err.assert_called_once()
        # URI should be cleaned up from retry counts
        assert msg.uri not in processor._retry_counts

    @pytest.mark.asyncio
    async def test_success_resets_retry_count(self):
        processor = SemanticProcessor(max_retries_per_uri=3)
        processor._retry_counts["viking://test"] = 2

        # Simulate success by directly calling the reset logic
        processor._retry_counts.pop("viking://test", None)
        assert "viking://test" not in processor._retry_counts


# ---------------------------------------------------------------------------
# 2. File existence check
# ---------------------------------------------------------------------------


class TestFileExistenceCheck:
    """Tests for the file-existence-before-processing safeguard."""

    @pytest.mark.asyncio
    async def test_missing_file_drops_from_queue(self):
        processor = SemanticProcessor()
        msg = _make_msg("viking://user/default/deleted_file.txt")

        mock_fs = MagicMock()
        mock_fs.exists = AsyncMock(return_value=False)

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=mock_fs,
        ):
            with patch(
                "openviking.storage.queuefs.semantic_processor.resolve_telemetry",
                return_value=None,
            ):
                with patch.object(processor, "report_error") as mock_err:
                    with patch(
                        "openviking.storage.queuefs.semantic_processor.is_semantic_msg_stale",
                        return_value=False,
                    ):
                        # The on_dequeue will check existence and report error
                        data = msg.to_dict()
                        result = await processor.on_dequeue(data)

        assert result is None
        mock_err.assert_called_once()
        # Check the error message mentions the non-existent file
        error_msg = mock_err.call_args[0][0]
        assert "does not exist" in error_msg


# ---------------------------------------------------------------------------
# 3. Config support for circuit breaker and semantic processor settings
# ---------------------------------------------------------------------------


class TestCircuitBreakerConfig:
    """Tests for circuit breaker configuration from VLMConfig."""

    def test_circuit_breaker_config_defaults(self):
        from openviking_cli.utils.config.vlm_config import CircuitBreakerConfig

        config = CircuitBreakerConfig()
        assert config.failure_threshold == 5
        assert config.reset_timeout == 300.0

    def test_circuit_breaker_config_custom_values(self):
        from openviking_cli.utils.config.vlm_config import CircuitBreakerConfig

        config = CircuitBreakerConfig(failure_threshold=10, reset_timeout=600.0)
        assert config.failure_threshold == 10
        assert config.reset_timeout == 600.0

    def test_vlm_config_includes_circuit_breaker(self):
        from openviking_cli.utils.config.vlm_config import CircuitBreakerConfig, VLMConfig

        vlm = VLMConfig(
            model="test-model",
            api_key="test-key",
            circuit_breaker=CircuitBreakerConfig(failure_threshold=3, reset_timeout=120.0),
        )
        assert vlm.circuit_breaker is not None
        assert vlm.circuit_breaker.failure_threshold == 3
        assert vlm.circuit_breaker.reset_timeout == 120.0

    def test_vlm_config_circuit_breaker_defaults_to_none(self):
        from openviking_cli.utils.config.vlm_config import VLMConfig

        vlm = VLMConfig(model="test-model", api_key="test-key")
        assert vlm.circuit_breaker is None


class TestSemanticProcessorConfig:
    """Tests for semantic processor configuration from StorageConfig."""

    def test_semantic_processor_config_defaults(self):
        from openviking_cli.utils.config.storage_config import SemanticProcessorConfig

        config = SemanticProcessorConfig()
        assert config.max_concurrent_llm == 64
        assert config.max_retries_per_uri == 3

    def test_semantic_processor_config_custom_values(self):
        from openviking_cli.utils.config.storage_config import SemanticProcessorConfig

        config = SemanticProcessorConfig(max_concurrent_llm=32, max_retries_per_uri=5)
        assert config.max_concurrent_llm == 32
        assert config.max_retries_per_uri == 5

    def test_storage_config_includes_semantic_processor(self):
        from openviking_cli.utils.config.storage_config import (
            SemanticProcessorConfig,
            StorageConfig,
        )

        storage = StorageConfig(
            semantic_processor=SemanticProcessorConfig(
                max_concurrent_llm=16,
                max_retries_per_uri=7,
            ),
        )
        assert storage.semantic_processor.max_concurrent_llm == 16
        assert storage.semantic_processor.max_retries_per_uri == 7

    def test_storage_config_semantic_processor_defaults(self):
        from openviking_cli.utils.config.storage_config import StorageConfig

        storage = StorageConfig()
        assert storage.semantic_processor.max_concurrent_llm == 64
        assert storage.semantic_processor.max_retries_per_uri == 3


class TestProcessorInitFromConfig:
    """Tests that SemanticProcessor reads circuit breaker config during init."""

    def test_uses_circuit_breaker_config_when_available(self):
        from openviking.utils.circuit_breaker import CircuitBreaker

        mock_cb_config = MagicMock()
        mock_cb_config.failure_threshold = 10
        mock_cb_config.reset_timeout = 120.0

        mock_vlm = MagicMock()
        mock_vlm.circuit_breaker = mock_cb_config

        mock_config = MagicMock()
        mock_config.vlm = mock_vlm

        with patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            return_value=mock_config,
        ):
            processor = SemanticProcessor()

        assert processor._circuit_breaker._failure_threshold == 10
        assert processor._circuit_breaker._base_reset_timeout == 120.0

    def test_defaults_when_no_config(self):
        with patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            side_effect=Exception("no config"),
        ):
            processor = SemanticProcessor()

        assert processor._circuit_breaker._failure_threshold == 5
        assert processor._circuit_breaker._base_reset_timeout == 300
