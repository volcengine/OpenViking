# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic queue lock resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from openviking.storage.transaction import NO_LOCK, LockHandoffRef, LockLease, OwnedLockLease


@dataclass
class SemanticLockScope:
    """Resolved lock scope for one semantic message."""

    lock: LockLease

    @classmethod
    async def resolve(
        cls,
        lock_handoff: Optional[LockHandoffRef],
        *,
        caller_lock: LockLease = NO_LOCK,
    ) -> "SemanticLockScope":
        if lock_handoff and caller_lock.active:
            raise ValueError("semantic lock must come from either message or caller, not both")
        if caller_lock is not NO_LOCK and not caller_lock.active:
            raise ValueError("caller semantic lock is inactive")
        if caller_lock.active:
            return cls(caller_lock.as_borrowed())
        if lock_handoff:
            return cls(await OwnedLockLease.from_handoff(lock_handoff))
        return cls(NO_LOCK)

    async def close(self) -> None:
        await self.lock.close()
