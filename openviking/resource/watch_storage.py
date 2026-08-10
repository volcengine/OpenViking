# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared constants and helpers for watch-task persistence storage."""

from __future__ import annotations

from openviking.core.namespace import uri_parts

WATCH_TASK_STORAGE_URI = "viking://resources/.watch_tasks.json"
WATCH_TASK_STORAGE_BAK_URI = "viking://resources/.watch_tasks.json.bak"
WATCH_TASK_STORAGE_TMP_URI = "viking://resources/.watch_tasks.json.tmp"

WATCH_TASK_CONTROL_URIS = frozenset(
    {
        WATCH_TASK_STORAGE_URI,
        WATCH_TASK_STORAGE_BAK_URI,
        WATCH_TASK_STORAGE_TMP_URI,
    }
)
_WATCH_TASK_CONTROL_NAMES = frozenset(uri.rsplit("/", 1)[-1] for uri in WATCH_TASK_CONTROL_URIS)


def is_watch_task_control_uri(uri: str) -> bool:
    """Return True when a URI points at internal watch-task control state."""
    if not isinstance(uri, str):
        return False

    # Match the logical URI segments used by storage, not the caller's spelling.
    parts = uri_parts(uri)
    return len(parts) == 2 and parts[0] == "resources" and parts[1] in _WATCH_TASK_CONTROL_NAMES
