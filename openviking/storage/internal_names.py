"""Shared internal file-name constants for Python storage code."""

from __future__ import annotations

MULTIWRITE_PATH_LOCK_FILE = ".path.ovlock"
MULTIWRITE_EXACT_LOCK_FILE_PREFIX = ".exact.ovlock."
MULTIWRITE_REDIRECT_FILE = ".redirect.json"
MULTIWRITE_SYNC_LOG_FILE = ".sync_log.json"

MULTIWRITE_INTERNAL_FILE_NAMES = frozenset(
    {
        MULTIWRITE_PATH_LOCK_FILE,
        MULTIWRITE_REDIRECT_FILE,
        MULTIWRITE_SYNC_LOG_FILE,
    }
)

STORAGE_INTERNAL_ENTRY_NAMES = frozenset(
    {
        "_system",
        "tasks",
        *MULTIWRITE_INTERNAL_FILE_NAMES,
    }
)

WEBDAV_RESERVED_FILENAMES = frozenset(
    {
        ".abstract.md",
        ".overview.md",
        ".relations.json",
        *MULTIWRITE_INTERNAL_FILE_NAMES,
    }
)
