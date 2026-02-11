# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
TransactionStore: Persistent storage for transaction records.

Stores transaction records as individual files in /transactions directory.
"""

import json
import os
from typing import Dict, Optional

from openviking.storage.transaction.filesystem import FileSystemBase
from openviking.storage.transaction.transaction_record import (
    TransactionRecord,
    TransactionStatus,
)
from openviking.utils.logger import get_logger

logger = get_logger(__name__)

TRANSACTIONS_DIR = "/transactions"


class TransactionStore:
    """Persistent storage for transaction records."""

    def __init__(self, fs: FileSystemBase):
        """Initialize TransactionStore.

        Args:
            fs: FileSystemBase instance for file operations
        """
        self._fs = fs
        self._cache: Dict[str, TransactionRecord] = {}

    async def initialize(self) -> None:
        """Initialize transaction store, create transactions directory if needed."""
        try:
            await self._fs.mkdir(TRANSACTIONS_DIR, exist_ok=True)
            logger.debug(f"[TransactionStore] Initialized with directory {TRANSACTIONS_DIR}")
        except Exception as e:
            logger.error(f"[TransactionStore] Failed to initialize: {e}")
            raise

    def _get_record_path(self, transaction_id: str) -> str:
        """Get file path for a transaction record.

        Args:
            transaction_id: Transaction ID

        Returns:
            File path for the transaction record
        """
        return os.path.join(TRANSACTIONS_DIR, transaction_id + ".json")

    async def add(self, record: TransactionRecord) -> None:
        """Add a transaction record.

        Args:
            record: Transaction record to add
        """
        path = self._get_record_path(record.id)
        await self._write_record(path, record)
        self._cache[record.id] = record
        logger.debug(f"[TransactionStore] Added transaction {record.id}")

    async def get(self, transaction_id: str) -> Optional[TransactionRecord]:
        """Get a transaction record by ID.

        Args:
            transaction_id: Transaction ID

        Returns:
            TransactionRecord if found, None otherwise
        """
        if transaction_id in self._cache:
            return self._cache[transaction_id]

        path = self._get_record_path(transaction_id)
        try:
            record = await self._read_record(path)
            if record:
                self._cache[transaction_id] = record
            return record
        except Exception as e:
            logger.error(f"[TransactionStore] Failed to get transaction {transaction_id}: {e}")
            return None

    async def update(self, record: TransactionRecord) -> None:
        """Update a transaction record.

        Args:
            record: Transaction record to update
        """
        path = self._get_record_path(record.id)
        await self._write_record(path, record)
        self._cache[record.id] = record

    async def delete(self, transaction_id: str) -> None:
        """Delete a transaction record.

        Args:
            transaction_id: Transaction ID to delete
        """
        path = self._get_record_path(transaction_id)
        try:
            await self._fs.rm(path, recursive=False)
            if transaction_id in self._cache:
                del self._cache[transaction_id]
            logger.debug(f"[TransactionStore] Deleted transaction {transaction_id}")
        except Exception as e:
            logger.error(f"[TransactionStore] Failed to delete transaction {transaction_id}: {e}")

    async def list_all(self) -> Dict[str, TransactionRecord]:
        """List all transaction records.

        Returns:
            Dictionary of transaction_id -> TransactionRecord
        """
        try:
            entries = await self._fs.ls(TRANSACTIONS_DIR)
            records = {}
            for entry in entries:
                name = entry.get("name", "")
                if name.endswith(".json"):
                    transaction_id = name[:-5]
                    record = await self.get(transaction_id)
                    if record:
                        records[transaction_id] = record
            return records
        except Exception as e:
            logger.error(f"[TransactionStore] Failed to list transactions: {e}")
            return {}

    async def cleanup(self, max_age: float = 86400) -> int:
        """Clean up completed/failed transactions older than max_age seconds.

        Args:
            max_age: Maximum age in seconds (default: 24 hours)

        Returns:
            Number of transactions cleaned up
        """
        import time

        current_time = time.time()
        cleaned = 0

        try:
            entries = await self._fs.ls(TRANSACTIONS_DIR)
            for entry in entries:
                name = entry.get("name", "")
                if name.endswith(".json"):
                    transaction_id = name[:-5]
                    record = await self.get(transaction_id)
                    if record and record.status in [
                        TransactionStatus.COMMIT,
                        TransactionStatus.FAIL,
                        TransactionStatus.RELEASED,
                    ]:
                        if current_time - record.updated_at > max_age:
                            await self.delete(transaction_id)
                            cleaned += 1
            logger.debug(f"[TransactionStore] Cleaned up {cleaned} transactions")
            return cleaned
        except Exception as e:
            logger.error(f"[TransactionStore] Failed to cleanup transactions: {e}")
            return cleaned

    async def _write_record(self, path: str, record: TransactionRecord) -> None:
        """Write transaction record to file.

        Args:
            path: File path
            record: Transaction record to write
        """
        data = record.to_dict()
        json_str = json.dumps(data, indent=2)
        await self._fs.write(path, json_str)

    async def _read_record(self, path: str) -> Optional[TransactionRecord]:
        """Read transaction record from file.

        Args:
            path: File path

        Returns:
            TransactionRecord if found, None otherwise
        """
        try:
            content = await self._fs.read(path)
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            data = json.loads(content)
            return TransactionRecord.from_dict(data)
        except Exception as e:
            logger.error(f"[TransactionStore] Failed to read record from {path}: {e}")
            return None
