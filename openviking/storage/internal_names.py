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


ROLLBACK_SAFE_ENTRY_NAMES = frozenset(
    {
        *STORAGE_INTERNAL_ENTRY_NAMES,
        *WEBDAV_RESERVED_FILENAMES,
    }
)


def is_rollback_safe_entry_name(name: str) -> bool:
    """Whether a directory entry is an internal marker rather than real content.

    Reserved targets materialize pathlock internals (``.path.ovlock``,
    ``.exact.ovlock.*``) and ingest stub sidecars (``.abstract.md`` /
    ``.overview.md``) before an import finishes, so a failed import can only
    roll back a directory whose entries are all internal markers.
    """
    if not name or name in {".", ".."}:
        return True
    return name in ROLLBACK_SAFE_ENTRY_NAMES or name.startswith(
        MULTIWRITE_EXACT_LOCK_FILE_PREFIX
    )
