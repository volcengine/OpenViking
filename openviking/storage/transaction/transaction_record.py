# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Transaction record and status definitions.

Defines the data structures for tracking transaction lifecycle and state.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class TransactionStatus(str, Enum):
    """Transaction status enumeration.

    Status machine: INIT -> AQUIRE -> EXEC -> COMMIT/FAIL -> RELEASING -> RELEASED
    """

    INIT = "INIT"  # Transaction initialized, waiting for lock acquisition
    AQUIRE = "AQUIRE"  # Acquiring lock resources
    EXEC = "EXEC"  # Transaction operation in progress
    COMMIT = "COMMIT"  # Transaction completed successfully
    FAIL = "FAIL"  # Transaction failed
    RELEASING = "RELEASING"  # Releasing lock resources
    RELEASED = "RELEASED"  # Lock resources fully released, transaction ended

    def __str__(self) -> str:
        return self.value


@dataclass
class TransactionRecord:
    """Transaction record for tracking transaction lifecycle.

    Attributes:
        id: Transaction ID in UUID format, uniquely identifies a transaction
        locks: List of lock paths held by this transaction
        status: Current transaction status
        init_info: Transaction initialization information
        rollback_info: Information for rollback operations
        undo_log: List of undo entries for rollback
        post_actions: Actions to execute after successful commit
        created_at: Creation timestamp (Unix timestamp in seconds)
        updated_at: Last update timestamp (Unix timestamp in seconds)
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    locks: List[str] = field(default_factory=list)
    status: TransactionStatus = field(default=TransactionStatus.INIT)
    init_info: Dict[str, Any] = field(default_factory=dict)
    rollback_info: Dict[str, Any] = field(default_factory=dict)
    undo_log: List[Any] = field(default_factory=list)
    post_actions: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def update_status(self, status: TransactionStatus) -> None:
        """Update transaction status and timestamp."""
        self.status = status
        self.updated_at = time.time()

    def add_lock(self, lock_path: str) -> None:
        """Add a lock to the transaction."""
        if lock_path not in self.locks:
            self.locks.append(lock_path)
            self.updated_at = time.time()

    def remove_lock(self, lock_path: str) -> None:
        """Remove a lock from the transaction."""
        if lock_path in self.locks:
            self.locks.remove(lock_path)
            self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Convert transaction record to dictionary."""
        return {
            "id": self.id,
            "locks": self.locks,
            "status": str(self.status),
            "init_info": self.init_info,
            "rollback_info": self.rollback_info,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_journal(self) -> Dict[str, Any]:
        """Serialize to journal format (includes undo_log and post_actions)."""
        from openviking.storage.transaction.undo import UndoEntry

        return {
            "id": self.id,
            "locks": self.locks,
            "status": str(self.status),
            "init_info": self.init_info,
            "undo_log": [e.to_dict() if isinstance(e, UndoEntry) else e for e in self.undo_log],
            "post_actions": self.post_actions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_journal(cls, data: Dict[str, Any]) -> "TransactionRecord":
        """Restore from journal format."""
        from openviking.storage.transaction.undo import UndoEntry

        status_str = data.get("status", "INIT")
        status = TransactionStatus(status_str) if isinstance(status_str, str) else status_str
        undo_log = [UndoEntry.from_dict(e) for e in data.get("undo_log", [])]

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            locks=data.get("locks", []),
            status=status,
            init_info=data.get("init_info", {}),
            rollback_info={},
            undo_log=undo_log,
            post_actions=data.get("post_actions", []),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransactionRecord":
        """Create transaction record from dictionary."""
        status_str = data.get("status", "INIT")
        status = TransactionStatus(status_str) if isinstance(status_str, str) else status_str

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            locks=data.get("locks", []),
            status=status,
            init_info=data.get("init_info", {}),
            rollback_info=data.get("rollback_info", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
