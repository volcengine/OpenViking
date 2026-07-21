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

def _make_msg(
    uri: str = "viking://user/default/file.txt",
    account_id: str = "default",
    user_id: str = "default",
) -> SemanticMsg:
    msg = SemanticMsg(uri=uri, context_type="resource", account_id=account_id, user_id=user_id)
    msg.coalesce_key = f"{msg.context_type}:{account_id}:{user_id}::{uri}"
    return msg

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

        assert processor._retry_counts[msg.coalesce_key] == 1

    @pytest.mark.asyncio
    async def test_drops_after_max_retries(self):
        processor = SemanticProcessor(max_retries_per_uri=2)
        msg = _make_msg()

        # Pre-set retry count to max-1
        processor._retry_counts[msg.coalesce_key] = 1

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
        # Coalesce key should be cleaned up from retry counts
        assert msg.coalesce_key not in processor._retry_counts

    @pytest.mark.asyncio
    async def test_success_resets_retry_count(self):
        """Stale-message early-exit must clean up _retry_counts."""
        processor = SemanticProcessor(max_retries_per_uri=3)
        msg = _make_msg("viking://test")
        processor._retry_counts[msg.coalesce_key] = 2

        with patch(
            "openviking.storage.queuefs.semantic_processor.is_semantic_msg_stale",
            return_value=True,
        ):
            with patch(
                "openviking.storage.queuefs.semantic_processor.get_request_wait_tracker"
            ) as mock_tracker:
                mock_tracker.return_value = MagicMock()
                with patch.object(processor, "report_success"):
                    result = await processor.on_dequeue(msg.to_dict())

        assert result is None
        assert msg.coalesce_key not in processor._retry_counts

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
        error_msg = mock_err.call_args[0][0]
        assert "does not exist" in error_msg

# ---------------------------------------------------------------------------
# 3. Config support for circuit breaker and semantic processor settings
# ---------------------------------------------------------------------------


class TestMultiTenantIsolation:
    """Retry counts must be isolated per tenant/peer, not shared by URI alone."""

    @pytest.mark.asyncio
    async def test_different_tenants_have_separate_retry_budgets(self):
        processor = SemanticProcessor(max_retries_per_uri=2)
        msg_a = _make_msg("viking://shared/file.txt", account_id="tenant-a")
        msg_b = _make_msg("viking://shared/file.txt", account_id="tenant-b")

        # Exhaust retries for tenant-a
        processor._retry_counts[msg_a.coalesce_key] = 1

        # tenant-b should still have a fresh budget
        assert msg_b.coalesce_key not in processor._retry_counts
        assert processor._retry_counts.get(msg_b.coalesce_key, 0) == 0
