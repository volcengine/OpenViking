# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
TransactionObserver: Transaction system observability tool.

Provides methods to observe and report transaction manager status.
"""

import time
from typing import Any, Dict

from openviking.storage.observers.base_observer import BaseObserver
from openviking.storage.transaction import TransactionManager
from openviking.storage.transaction.transaction_record import TransactionStatus
from openviking_cli.utils import run_async
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class TransactionObserver(BaseObserver):
    """
    TransactionObserver: System observability tool for transaction management.

    Provides methods to query transaction status and format output.
    """

    def __init__(self, transaction_manager: TransactionManager):
        """Initialize transaction observer.

        Args:
            transaction_manager: Transaction manager instance to observe
        """
        self._transaction_manager = transaction_manager

    async def get_status_table_async(self) -> str:
        """Get transaction status table asynchronously.

        Returns:
            Formatted table string showing transaction status
        """
        if not self._transaction_manager:
            return "Transaction manager not initialized."

        transactions = self._transaction_manager.get_active_transactions()

        if not transactions:
            return "No active transactions."

        return self._format_status_as_table(transactions)

    def get_status_table(self) -> str:
        """Get transaction status table synchronously.

        Returns:
            Formatted table string showing transaction status
        """
        return run_async(self.get_status_table_async())

    def __str__(self) -> str:
        """String representation returns status table.

        Returns:
            Formatted table string
        """
        return self.get_status_table()

    def _format_status_as_table(self, transactions: Dict[str, Any]) -> str:
        """Format transaction statuses as a table.

        Args:
            transactions: Dict mapping transaction IDs to TransactionRecord

        Returns:
            Formatted table string
        """
        from tabulate import tabulate

        data = []

        # Group transactions by status
        status_counts = {
            TransactionStatus.INIT: 0,
            TransactionStatus.AQUIRE: 0,
            TransactionStatus.EXEC: 0,
            TransactionStatus.COMMIT: 0,
            TransactionStatus.FAIL: 0,
            TransactionStatus.RELEASING: 0,
            TransactionStatus.RELEASED: 0,
        }

        for tx_id, tx in transactions.items():
            duration = time.time() - tx.created_at
            duration_str = f"{duration:.1f}s"

            status_counts[tx.status] += 1

            data.append(
                {
                    "Transaction ID": tx_id[:8] + "...",
                    "Status": str(tx.status),
                    "Locks": len(tx.locks),
                    "Duration": duration_str,
                    "Created": time.strftime("%H:%M:%S", time.localtime(tx.created_at)),
                }
            )

        status_priority = {
            TransactionStatus.EXEC: 0,
            TransactionStatus.AQUIRE: 1,
            TransactionStatus.RELEASING: 2,
            TransactionStatus.INIT: 3,
            TransactionStatus.COMMIT: 4,
            TransactionStatus.FAIL: 5,
            TransactionStatus.RELEASED: 6,
        }

        data.sort(key=lambda x: status_priority.get(TransactionStatus(x["Status"]), 99))

        total = len(transactions)
        total_locks = sum(len(tx.locks) for tx in transactions.values())

        summary_row = {
            "Transaction ID": f"TOTAL ({total})",
            "Status": "",
            "Locks": total_locks,
            "Duration": "",
            "Created": "",
        }
        data.append(summary_row)

        return tabulate(data, headers="keys", tablefmt="pretty")

    def is_healthy(self) -> bool:
        """Check if transaction system is healthy.

        Returns:
            True if system is healthy, False otherwise
        """
        return not self.has_errors()

    def has_errors(self) -> bool:
        """Check if transaction system has any errors.

        Returns:
            True if errors (failed transactions) exist, False otherwise
        """
        if not self._transaction_manager:
            return True

        transactions = self._transaction_manager.get_active_transactions()

        # Check for failed transactions
        for tx_id, tx in transactions.items():
            if tx.status == TransactionStatus.FAIL:
                logger.warning(f"Found failed transaction: {tx_id}")
                return True

        return False

    def get_failed_transactions(self) -> Dict[str, Any]:
        """Get all failed transactions.

        Returns:
            Dict mapping transaction IDs to failed TransactionRecord
        """
        if not self._transaction_manager:
            return {}

        transactions = self._transaction_manager.get_active_transactions()
        return {
            tx_id: tx for tx_id, tx in transactions.items() if tx.status == TransactionStatus.FAIL
        }

    def get_hanging_transactions(self, timeout_threshold: int = 300) -> Dict[str, Any]:
        """Get transactions that have been running longer than threshold.

        Args:
            timeout_threshold: Timeout threshold in seconds (default: 300 = 5 minutes)

        Returns:
            Dict mapping transaction IDs to TransactionRecord that exceed threshold
        """
        if not self._transaction_manager:
            return {}

        transactions = self._transaction_manager.get_active_transactions()
        current_time = time.time()

        return {
            tx_id: tx
            for tx_id, tx in transactions.items()
            if current_time - tx.created_at > timeout_threshold
        }

    def get_status_summary(self) -> Dict[str, int]:
        """Get summary of transaction counts by status.

        Returns:
            Dict mapping status strings to counts
        """
        if not self._transaction_manager:
            return {}

        transactions = self._transaction_manager.get_active_transactions()

        summary = {
            "INIT": 0,
            "AQUIRE": 0,
            "EXEC": 0,
            "COMMIT": 0,
            "FAIL": 0,
            "RELEASING": 0,
            "RELEASED": 0,
            "TOTAL": 0,
        }

        for tx in transactions.values():
            summary[str(tx.status)] += 1
            summary["TOTAL"] += 1

        return summary
