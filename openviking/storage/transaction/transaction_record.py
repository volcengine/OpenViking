# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
TransactionRecord: Data class for transaction records.

Stores transaction metadata including locks, status, and rollback information.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List


class TransactionStatus:
    """Transaction status constants."""

    INIT = "INIT"
    ACQUIRE = "ACQUIRE"
    EXEC = "EXEC"
    COMMIT = "COMMIT"
    FAIL = "FAIL"
    RELEASING = "RELEASING"
    RELEASED = "RELEASED"


@dataclass
class TransactionRecord:
    """Transaction record data class.

    Attributes:
        id: Transaction ID (UUID format)
        locks: List of locked paths
        status: Current transaction status
        init_info: Transaction initialization information
        rollback_info: Information for rollback operations
        created_at: Creation timestamp (Unix timestamp)
        updated_at: Last update timestamp (Unix timestamp)
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    locks: List[str] = field(default_factory=list)
    status: str = TransactionStatus.INIT
    init_info: Dict[str, Any] = field(default_factory=dict)
    rollback_info: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def update_status(self, new_status: str) -> None:
        """Update transaction status and timestamp.

        Args:
            new_status: New transaction status
        """
        self.status = new_status
        self.updated_at = time.time()

    def add_lock(self, path: str) -> None:
        """Add a lock to the transaction.

        Args:
            path: Path to lock
        """
        if path not in self.locks:
            self.locks.append(path)
        self.updated_at = time.time()

    def remove_lock(self, path: str) -> None:
        """Remove a lock from the transaction.

        Args:
            path: Path to unlock
        """
        if path in self.locks:
            self.locks.remove(path)
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Convert transaction record to dictionary.

        Returns:
            Dictionary representation of the transaction record
        """
        return {
            "id": self.id,
            "locks": self.locks,
            "status": self.status,
            "init_info": self.init_info,
            "rollback_info": self.rollback_info,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransactionRecord":
        """Create transaction record from dictionary.

        Args:
            data: Dictionary containing transaction record data

        Returns:
            TransactionRecord instance
        """
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            locks=data.get("locks", []),
            status=data.get("status", TransactionStatus.INIT),
            init_info=data.get("init_info", {}),
            rollback_info=data.get("rollback_info", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
