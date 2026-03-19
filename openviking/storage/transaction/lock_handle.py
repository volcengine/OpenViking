# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Lock handle and LockOwner protocol for PathLock integration."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class LockOwner(Protocol):
    """Minimal interface that PathLock requires from its caller."""

    id: str
    locks: list[str]

    def add_lock(self, path: str) -> None: ...
    def remove_lock(self, path: str) -> None: ...


@dataclass
class LockHandle:
    """Identifies a lock holder. PathLock uses ``id`` to generate fencing tokens
    and ``locks`` to track acquired lock files."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    locks: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def add_lock(self, lock_path: str) -> None:
        if lock_path not in self.locks:
            self.locks.append(lock_path)

    def remove_lock(self, lock_path: str) -> None:
        if lock_path in self.locks:
            self.locks.remove(lock_path)
