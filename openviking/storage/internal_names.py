"""Shared internal file-name constants for Python storage code."""

from __future__ import annotations

MULTIWRITE_PATH_LOCK_FILE = ".path.ovlock"
MULTIWRITE_EXACT_LOCK_FILE_PREFIX = ".exact.ovlock."
MULTIWRITE_REDIRECT_FILE = ".redirect.json"
MULTIWRITE_SYNC_LOG_FILE = ".sync_log.json"
RELATION_TABLE_FILENAME = ".relations.json"

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
        RELATION_TABLE_FILENAME,
        *MULTIWRITE_INTERNAL_FILE_NAMES,
    }
)


def is_relation_sidecar_name(name: str) -> bool:
    """Return whether a leaf name is a relation table sidecar."""
    return bool(name) and name.endswith(RELATION_TABLE_FILENAME)


def file_relation_sidecar_path(source_path: str) -> str:
    """Return the sibling relation sidecar path for a file source."""
    return f"{source_path.rstrip('/')}{RELATION_TABLE_FILENAME}"


def relation_table_path(source_path: str, *, is_dir: bool) -> str:
    """Return the relation table path for either a file or directory source."""
    if is_dir:
        return f"{source_path.rstrip('/')}/{RELATION_TABLE_FILENAME}"
    return file_relation_sidecar_path(source_path)


def is_storage_internal_entry_name(name: str) -> bool:
    """Return whether a storage directory entry is internal metadata."""
    return name in STORAGE_INTERNAL_ENTRY_NAMES or is_relation_sidecar_name(name)


def is_webdav_reserved_filename(name: str) -> bool:
    """Return whether a WebDAV path component must not be exposed."""
    return name in WEBDAV_RESERVED_FILENAMES or is_relation_sidecar_name(name)
