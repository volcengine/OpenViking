# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for post_actions execution and replay."""

from unittest.mock import AsyncMock, MagicMock, patch

from openviking.storage.transaction.transaction_manager import TransactionManager


class TestPostActions:
    def _make_manager(self):
        agfs = MagicMock()
        manager = TransactionManager(agfs_client=agfs, timeout=3600)
        manager._journal = MagicMock()
        return manager, agfs

    async def test_execute_enqueue_semantic(self):
        manager, _ = self._make_manager()

        mock_queue = AsyncMock()
        mock_queue_manager = MagicMock()
        mock_queue_manager.get_queue.return_value = mock_queue

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            await manager._execute_post_actions(
                [
                    {
                        "type": "enqueue_semantic",
                        "params": {
                            "uri": "viking://resources/test",
                            "context_type": "resource",
                            "account_id": "acc-1",
                        },
                    }
                ]
            )

        mock_queue.enqueue.assert_called_once()
        msg = mock_queue.enqueue.call_args[0][0]
        assert msg.uri == "viking://resources/test"
        assert msg.context_type == "resource"
        assert msg.account_id == "acc-1"

    async def test_execute_unknown_action_logged(self):
        manager, _ = self._make_manager()
        # Should not raise, just log
        await manager._execute_post_actions(
            [
                {"type": "unknown_action", "params": {}},
            ]
        )

    async def test_execute_multiple_actions(self):
        manager, _ = self._make_manager()

        mock_queue = AsyncMock()
        mock_queue_manager = MagicMock()
        mock_queue_manager.get_queue.return_value = mock_queue

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            await manager._execute_post_actions(
                [
                    {
                        "type": "enqueue_semantic",
                        "params": {
                            "uri": "viking://a",
                            "context_type": "resource",
                            "account_id": "acc-1",
                        },
                    },
                    {
                        "type": "enqueue_semantic",
                        "params": {
                            "uri": "viking://b",
                            "context_type": "memory",
                            "account_id": "acc-2",
                        },
                    },
                ]
            )

        assert mock_queue.enqueue.call_count == 2

    async def test_post_action_failure_does_not_crash(self):
        manager, _ = self._make_manager()

        mock_queue_manager = MagicMock()
        mock_queue_manager.get_queue.side_effect = Exception("queue not available")

        with patch(
            "openviking.storage.queuefs.get_queue_manager",
            return_value=mock_queue_manager,
        ):
            # Should not raise
            await manager._execute_post_actions(
                [
                    {
                        "type": "enqueue_semantic",
                        "params": {
                            "uri": "viking://test",
                            "context_type": "resource",
                            "account_id": "",
                        },
                    },
                ]
            )
