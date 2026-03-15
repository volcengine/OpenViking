# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from pydantic import BaseModel, Field


class TransactionConfig(BaseModel):
    """Configuration for the transaction mechanism.

    By default, lock acquisition does not wait (``lock_timeout=0``): if a
    conflicting lock is held the operation fails immediately with
    ``LockAcquisitionError``.  Set ``lock_timeout`` to a positive value to
    allow the caller to block and retry for up to that many seconds.
    """

    lock_timeout: float = Field(
        default=0.0,
        description=(
            "Path lock acquisition timeout (seconds). "
            "0 = fail immediately if locked (default). "
            "> 0 = wait/retry up to this many seconds before raising LockAcquisitionError."
        ),
    )

    lock_expire: float = Field(
        default=300.0,
        description=(
            "Stale lock expiry threshold (seconds). "
            "Locks held longer than this by a crashed process are force-released."
        ),
    )

    max_parallel_locks: int = Field(
        default=8,
        description="Maximum parallel lock operations during recursive rm/mv.",
    )

    model_config = {"extra": "forbid"}
