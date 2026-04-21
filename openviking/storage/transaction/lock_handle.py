# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lock handle and LockOwner protocol for PathLock integration."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


def _new_lock_id() -> str:
    return str(uuid.uuid4())


@runtime_checkable
class LockOwner(Protocol):
    """Minimal interface that PathLock requires from its caller."""

    id: str
    locks: list[str]

    def add_lock(self, path: str) -> None:
        raise NotImplementedError

    def remove_lock(self, path: str) -> None:
        raise NotImplementedError


@dataclass
class LockHandle:
    """Identifies a lock holder. PathLock uses ``id`` to generate fencing tokens
    and ``locks`` to track acquired lock files."""

    id: str = field(default_factory=_new_lock_id)
    locks: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.last_active_at = self.created_at

    def add_lock(self, lock_path: str) -> None:
        if lock_path not in self.locks:
            self.locks.append(lock_path)

    def remove_lock(self, lock_path: str) -> None:
        if lock_path in self.locks:
            self.locks.remove(lock_path)
