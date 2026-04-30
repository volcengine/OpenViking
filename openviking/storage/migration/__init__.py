# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Migration utilities for embedding model transitions.

This package provides tools to migrate embedding data between different
model providers, dimensions, and storage backends.
"""

from .blue_green_adapter import DualWriteAdapter
from .controller import InvalidTransitionError, MigrationController
from .reindex_engine import ReindexEngine
from .state import (
    MigrationPhase,
    MigrationState,
    MigrationStateFile,
    MigrationStateManager,
    ReindexProgress,
)

# rollback functions are imported lazily inside controller.py to avoid
# import-ordering issues during test collection.
# See: from .rollback import ...

__all__ = [
    "DualWriteAdapter",
    "InvalidTransitionError",
    "MigrationController",
    "MigrationPhase",
    "MigrationState",
    "MigrationStateFile",
    "MigrationStateManager",
    "ReindexEngine",
    "ReindexProgress",
]
