"""Shared internal file-name constants for Python storage code."""

from __future__ import annotations

import json

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

# Name-only markers: presence alone never counts as user/import content.
ROLLBACK_SAFE_ENTRY_NAMES = frozenset(
    {
        *STORAGE_INTERNAL_ENTRY_NAMES,
    }
)

# Derived sidecars: empty / placeholder bodies are rollback-safe; real text is not.
ROLLBACK_CONTENT_GATED_ENTRY_NAMES = frozenset(
    {
        ".abstract.md",
        ".overview.md",
        ".relations.json",
    }
)

# Markers VikingFS / listing paths surface before semantic generation finishes.
_ABSTRACT_NOT_READY_MARKERS = (
    "[.abstract.md is not ready]",
    "[Directory abstract is not ready]",
)
_OVERVIEW_NOT_READY_MARKERS = (
    "[Directory overview is not ready]",
)


def is_rollback_safe_entry_name(name: str) -> bool:
    """Whether a directory entry is an internal marker by name alone.

    Pathlock / redirect / sync bookkeeping are structural. Ingest sidecars
    (``.abstract.md`` / ``.overview.md`` / ``.relations.json``) are
    content-gated — use :func:`is_rollback_safe_sidecar_content`.
    """
    if not name or name in {".", ".."}:
        return True
    return name in ROLLBACK_SAFE_ENTRY_NAMES or name.startswith(
        MULTIWRITE_EXACT_LOCK_FILE_PREFIX
    )


def is_rollback_content_gated_entry_name(name: str) -> bool:
    """Whether rollback safety depends on file contents, not just the name."""
    return name in ROLLBACK_CONTENT_GATED_ENTRY_NAMES


def _is_not_ready_directory_sentinel(text: str, markers: tuple[str, ...]) -> bool:
    """Match VikingFS not-ready placeholders without dropping real summaries.

    Shapes seen in the wild:
    - bare marker (listing fallback)
    - ``# <uri>`` / ``# <name>`` header plus the marker (read-path fallback)
    """
    head = text.rstrip()
    for marker in markers:
        if head == marker:
            return True
        if not head.endswith(marker):
            continue
        prefix = head[: -len(marker)].strip()
        if not prefix:
            return True
        # Single markdown H1 with no further body before the marker.
        if prefix.startswith("#") and "\n" not in prefix:
            return True
    return False


def _is_empty_relations_stub(text: str) -> bool:
    """True for empty or JSON-empty relations sidecars (``{}`` / ``[]``)."""
    stripped = text.strip()
    if not stripped:
        return True
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError):
        return False
    return parsed in ({}, [])


def is_rollback_safe_sidecar_content(name: str, content: str | bytes | None) -> bool:
    """True when a content-gated sidecar is empty or still a not-ready stub.

    Non-empty semantic / OKF / relations bodies block reserved-target rollback
    so filled sidecars are not deleted with the directory.
    """
    if name not in ROLLBACK_CONTENT_GATED_ENTRY_NAMES:
        return False
    if content is None:
        return True
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = str(content)
    stripped = text.strip()
    if not stripped:
        return True
    if name == ".abstract.md":
        return _is_not_ready_directory_sentinel(stripped, _ABSTRACT_NOT_READY_MARKERS)
    if name == ".overview.md":
        return _is_not_ready_directory_sentinel(stripped, _OVERVIEW_NOT_READY_MARKERS)
    if name == ".relations.json":
        return _is_empty_relations_stub(stripped)
    return False
