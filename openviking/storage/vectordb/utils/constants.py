# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Constants definition module

This module centralizes all constants used in VikingVectorIndex,
avoiding magic strings scattered throughout the code, improving code maintainability.
"""

from enum import Enum


# ==================== Table name constants ====================
class TableNames(str, Enum):
    """Storage table name enum"""

    CANDIDATES = "C"  # Candidate data table
    DELTA = "D"  # Delta data table
    TTL = "T"  # TTL expiration time table


# ==================== Special field names ====================
class SpecialFields(str, Enum):
    """Special field name enum"""

    AUTO_ID = "AUTO_ID"  # Auto-generated primary key field name


# ==================== Aggregate operation related ====================
class AggregateKeys(str, Enum):
    """Aggregate operation related key names"""

    TOTAL_COUNT_INTERNAL = "__total_count__"  # Internal total key name
    TOTAL_COUNT_EXTERNAL = "_total"  # External return total key name


# ==================== Index related constants ====================
class IndexFileMarkers(str, Enum):
    """Index file markers"""

    WRITE_DONE = ".write_done"  # Index write complete marker file suffix


# ==================== Scheduler related constants ====================
DEFAULT_TTL_CLEANUP_SECONDS = 0  # TTL expired data cleanup interval (seconds)
DEFAULT_INDEX_MAINTENANCE_SECONDS = 30  # Index maintenance task interval (seconds)

# Environment variable names
ENV_TTL_CLEANUP_SECONDS = "VECTORDB_TTL_CLEANUP_SECONDS"
ENV_INDEX_MAINTENANCE_SECONDS = "VECTORDB_INDEX_MAINTENANCE_SECONDS"


# ==================== Other constants ====================
DEFAULT_PROJECT_NAME = "default"  # Default project name
DEFAULT_LIMIT = 10  # Default search return result count
STORAGE_DIR_NAME = "store"  # Storage directory name
