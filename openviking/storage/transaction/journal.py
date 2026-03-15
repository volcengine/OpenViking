# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Transaction journal for crash recovery.

Persists transaction state to AGFS so that incomplete transactions can be
detected and recovered after a process restart.
"""

import json
from typing import Any, Dict, List

from openviking.pyagfs import AGFSClient
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# Journal root path (global, not behind VikingFS URI mapping)
_JOURNAL_ROOT = "/local/_system/transactions"


class TransactionJournal:
    """Persists transaction records to AGFS for crash recovery.

    Journal files live at ``/local/_system/transactions/{tx_id}/journal.json``.
    """

    def __init__(self, agfs: AGFSClient):
        self._agfs = agfs

    def _tx_dir(self, tx_id: str) -> str:
        return f"{_JOURNAL_ROOT}/{tx_id}"

    def _journal_path(self, tx_id: str) -> str:
        return f"{_JOURNAL_ROOT}/{tx_id}/journal.json"

    def _ensure_dir(self, path: str) -> None:
        """Create directory, ignoring already-exists errors."""
        try:
            self._agfs.mkdir(path)
        except Exception as e:
            logger.warning(f"[Journal] mkdir {path}: {e}")

    def write(self, data: Dict[str, Any]) -> None:
        """Create a new journal entry for a transaction.

        Args:
            data: Serialized transaction record (from TransactionRecord.to_journal()).
        """
        tx_id = data["id"]
        self._ensure_dir("/local/_system")
        self._ensure_dir(_JOURNAL_ROOT)
        self._ensure_dir(self._tx_dir(tx_id))
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._agfs.write(self._journal_path(tx_id), payload)
        logger.info(f"[Journal] Written: {self._journal_path(tx_id)}")

    def update(self, data: Dict[str, Any]) -> None:
        """Overwrite an existing journal entry.

        Args:
            data: Updated serialized transaction record.
        """
        tx_id = data["id"]
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._agfs.write(self._journal_path(tx_id), payload)

    def read(self, tx_id: str) -> Dict[str, Any]:
        """Read a journal entry.

        Args:
            tx_id: Transaction ID.

        Returns:
            Parsed journal data.

        Raises:
            FileNotFoundError: If journal does not exist.
        """
        content = self._agfs.cat(self._journal_path(tx_id))
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        return json.loads(content)

    def delete(self, tx_id: str) -> None:
        """Delete a transaction's journal directory.

        Args:
            tx_id: Transaction ID.
        """
        try:
            self._agfs.rm(self._tx_dir(tx_id), recursive=True)
            logger.debug(f"[Journal] Deleted journal for tx {tx_id}")
        except Exception as e:
            logger.warning(f"[Journal] Failed to delete journal for tx {tx_id}: {e}")

    def list_all(self) -> List[str]:
        """List all transaction IDs that have journal entries.

        Returns:
            List of transaction ID strings.
        """
        try:
            entries = self._agfs.ls(_JOURNAL_ROOT)
            tx_ids = []
            if isinstance(entries, list):
                for entry in entries:
                    name = entry.get("name", "") if isinstance(entry, dict) else str(entry)
                    if name and name not in (".", "..") and entry.get("isDir", True):
                        tx_ids.append(name)
            return tx_ids
        except Exception:
            return []
