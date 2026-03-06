# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Transaction module for OpenViking.

Provides transaction management and lock mechanisms for data operations.
"""

from openviking.storage.transaction.context_manager import TransactionContext
from openviking.storage.transaction.journal import TransactionJournal
from openviking.storage.transaction.path_lock import PathLock
from openviking.storage.transaction.transaction_manager import (
    TransactionManager,
    get_transaction_manager,
    init_transaction_manager,
)
from openviking.storage.transaction.transaction_record import (
    TransactionRecord,
    TransactionStatus,
)
from openviking.storage.transaction.undo import UndoEntry, execute_rollback

__all__ = [
    "PathLock",
    "TransactionContext",
    "TransactionJournal",
    "TransactionManager",
    "TransactionRecord",
    "TransactionStatus",
    "UndoEntry",
    "execute_rollback",
    "get_transaction_manager",
    "init_transaction_manager",
]
