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


def is_storage_internal_name(name: str) -> bool:
    """Return whether ``name`` is multi-write metadata owned by the storage layer.

    Mirrors ``is_hidden_internal_name`` in ``crates/ragfs/src/core/internal_names.rs``:
    the directory lock, exact (sidecar) locks, redirect and sync-log files that RAGFS
    keeps next to user content in any directory. Such an entry is hidden from every
    listing and must never be created, written, copied or moved by a user.

    The account-root internal directories (``/local/{account}/_system`` and
    ``/local/{account}/tasks``) are deliberately not part of this predicate: root
    listings use ``VikingURI.LISTABLE_SCOPES`` as a whitelist, and below the root a
    user directory that happens to be called ``tasks`` or ``_system`` is ordinary
    content.
    """
    return name in MULTIWRITE_INTERNAL_FILE_NAMES or name.startswith(
        MULTIWRITE_EXACT_LOCK_FILE_PREFIX
    )


WEBDAV_RESERVED_FILENAMES = frozenset(
    {
        ".abstract.md",
        ".overview.md",
        ".relations.json",
        *MULTIWRITE_INTERNAL_FILE_NAMES,
    }
)
