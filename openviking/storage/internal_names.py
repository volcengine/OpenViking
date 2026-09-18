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

# Entries hidden from listings at every level: the multi-write lock/redirect/sync-log
# files the storage layer creates next to user content. The account-root internal
# directories (/local/{account}/_system, /local/{account}/tasks) are not listed here:
# root listings use VikingURI.LISTABLE_SCOPES as a whitelist, and below the root a user
# directory that happens to be called "tasks" or "_system" is ordinary content.
STORAGE_INTERNAL_ENTRY_NAMES = frozenset(MULTIWRITE_INTERNAL_FILE_NAMES)

WEBDAV_RESERVED_FILENAMES = frozenset(
    {
        ".abstract.md",
        ".overview.md",
        ".relations.json",
        *MULTIWRITE_INTERNAL_FILE_NAMES,
    }
)
