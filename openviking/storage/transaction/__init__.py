# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Transaction module for OpenViking.

Provides path-lock management.
"""

from openviking.storage.transaction.lock_context import LockContext
from openviking.storage.transaction.lock_handle import LockHandle, LockOwner
from openviking.storage.transaction.lock_lease import (
    NO_LOCK,
    BorrowedLockLease,
    LockHandoffRef,
    LockLease,
    OwnedLockLease,
)
from openviking.storage.transaction.lock_manager import (
    LOCK_TIMEOUT_DEFAULT,
    LockManager,
    get_lock_handle_async,
    get_lock_manager,
    init_lock_manager,
    release_all_locks,
    reset_lock_manager,
)
from openviking.storage.transaction.path_lock import PathLockEngine

__all__ = [
    "BorrowedLockLease",
    "LockContext",
    "LockHandle",
    "LockHandoffRef",
    "LockLease",
    "LOCK_TIMEOUT_DEFAULT",
    "LockManager",
    "LockOwner",
    "NO_LOCK",
    "OwnedLockLease",
    "PathLockEngine",
    "get_lock_handle_async",
    "get_lock_manager",
    "init_lock_manager",
    "release_all_locks",
    "reset_lock_manager",
]
