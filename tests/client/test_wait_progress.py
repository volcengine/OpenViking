# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for progress_callback support in wait_processed() chain."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.storage.queuefs.named_queue import QueueStatus


# ============= TestQueueStatusTotal =============


class TestQueueStatusTotal:
    """Tests for QueueStatus.total property."""

    def test_total_all_zeros(self):
        status = QueueStatus()
        assert status.total == 0

    def test_total_only_pending(self):
        status = QueueStatus(pending=5)
        assert status.total == 5

    def test_total_only_processed(self):
        status = QueueStatus(processed=10)
        assert status.total == 10

    def test_total_mixed(self):
        status = QueueStatus(pending=2, in_progress=3, processed=4, error_count=1)
        assert status.total == 10

    def test_total_with_errors(self):
        status = QueueStatus(processed=7, error_count=3)
        assert status.total == 10


# ============= TestQueueManagerProgressCallback =============


class TestQueueManagerProgressCallback:
    """Tests for QueueManager.wait_complete() progress_callback."""

    @pytest.mark.asyncio
    async def test_callback_called_with_statuses(self):
        """progress_callback should be called with statuses dict each poll iteration."""
        from openviking.storage.queuefs.queue_manager import QueueManager

        qm = QueueManager.__new__(QueueManager)
        qm._queues = {}
        qm._started = True
        qm._agfs = None

        call_count = 0
        statuses_in_progress = QueueStatus(pending=3, in_progress=1, processed=1)
        statuses_complete = QueueStatus(pending=0, in_progress=0, processed=5)

        async def mock_check_status(queue_name=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return {"Embedding": statuses_in_progress}
            return {"Embedding": statuses_complete}

        qm.check_status = mock_check_status

        callback = MagicMock()
        result = await qm.wait_complete(poll_interval=0.01, progress_callback=callback)

        # callback should be called for non-complete iterations (2 times)
        assert callback.call_count == 2
        # Each call should receive the statuses dict
        for call_args in callback.call_args_list:
            assert "Embedding" in call_args[0][0]

        # Final result should be the complete statuses
        assert result["Embedding"].is_complete

    @pytest.mark.asyncio
    async def test_none_callback_no_error(self):
        """None progress_callback should not cause errors."""
        from openviking.storage.queuefs.queue_manager import QueueManager

        qm = QueueManager.__new__(QueueManager)
        qm._queues = {}
        qm._started = True
        qm._agfs = None

        statuses_complete = QueueStatus(pending=0, in_progress=0, processed=5)

        async def mock_check_status(queue_name=None):
            return {"Embedding": statuses_complete}

        qm.check_status = mock_check_status

        result = await qm.wait_complete(
            poll_interval=0.01, progress_callback=None
        )
        assert result["Embedding"].is_complete

    @pytest.mark.asyncio
    async def test_timeout_with_callback(self):
        """TimeoutError should still be raised even with progress_callback."""
        from openviking.storage.queuefs.queue_manager import QueueManager

        qm = QueueManager.__new__(QueueManager)
        qm._queues = {}
        qm._started = True
        qm._agfs = None

        statuses_in_progress = QueueStatus(pending=3, in_progress=1)

        async def mock_check_status(queue_name=None):
            return {"Embedding": statuses_in_progress}

        qm.check_status = mock_check_status
        callback = MagicMock()

        with pytest.raises(TimeoutError):
            await qm.wait_complete(
                timeout=0.05,
                poll_interval=0.01,
                progress_callback=callback,
            )

        # callback should have been called at least once
        assert callback.call_count >= 1


# ============= TestResourceServiceProgressCallback =============


class TestResourceServiceProgressCallback:
    """Tests for ResourceService.wait_processed() progress_callback adapter."""

    @pytest.mark.asyncio
    async def test_adapter_converts_to_dict(self):
        """progress_callback adapter should convert QueueStatus to dict with total field."""
        from openviking.service.resource_service import ResourceService

        service = ResourceService()

        captured = []

        def user_callback(statuses_dict):
            captured.append(statuses_dict)

        statuses_in_progress = QueueStatus(
            pending=2, in_progress=1, processed=3, error_count=1
        )
        statuses_complete = QueueStatus(
            pending=0, in_progress=0, processed=6, error_count=1
        )

        call_count = 0

        async def mock_wait_complete(timeout=None, progress_callback=None):
            nonlocal call_count
            # Simulate one in-progress poll before completion
            if progress_callback is not None:
                progress_callback({"Embedding": statuses_in_progress})
            return {"Embedding": statuses_complete}

        with patch(
            "openviking.service.resource_service.get_queue_manager"
        ) as mock_get_qm:
            mock_qm = MagicMock()
            mock_qm.wait_complete = mock_wait_complete
            mock_get_qm.return_value = mock_qm

            result = await service.wait_processed(
                progress_callback=user_callback
            )

        # Verify the adapter converted QueueStatus to dict
        assert len(captured) == 1
        emb = captured[0]["Embedding"]
        assert emb["pending"] == 2
        assert emb["in_progress"] == 1
        assert emb["processed"] == 3
        assert emb["error_count"] == 1
        assert emb["total"] == 7

    @pytest.mark.asyncio
    async def test_backward_compat_no_callback(self):
        """wait_processed() without progress_callback should work as before."""
        from openviking.service.resource_service import ResourceService

        service = ResourceService()

        statuses_complete = QueueStatus(
            pending=0, in_progress=0, processed=5, error_count=0, errors=[]
        )

        async def mock_wait_complete(timeout=None, progress_callback=None):
            assert progress_callback is None
            return {"Embedding": statuses_complete}

        with patch(
            "openviking.service.resource_service.get_queue_manager"
        ) as mock_get_qm:
            mock_qm = MagicMock()
            mock_qm.wait_complete = mock_wait_complete
            mock_get_qm.return_value = mock_qm

            result = await service.wait_processed()

        assert result["Embedding"]["processed"] == 5
        assert result["Embedding"]["error_count"] == 0
