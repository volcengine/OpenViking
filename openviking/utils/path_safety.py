# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared helpers for safe user-supplied path handling."""

import re

_UNSAFE_REL_PATH_RE = re.compile(r"(^|[\\/])\.\.($|[\\/])")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def sanitize_relative_viking_path(rel_path: str) -> str:
    """Normalize a relative path for use inside Viking URI paths.

    The returned path always uses forward slashes. Absolute paths, Windows
    drive-prefixed paths, and parent-directory traversal are rejected with
    OS-independent checks.
    """
    if not rel_path:
        raise ValueError(f"Unsafe relative path rejected: {rel_path!r}")
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise ValueError(f"Unsafe relative path rejected: {rel_path}")
    if _WINDOWS_DRIVE_RE.match(rel_path):
        raise ValueError(f"Unsafe relative path rejected: {rel_path}")
    if _UNSAFE_REL_PATH_RE.search(rel_path):
        raise ValueError(f"Unsafe relative path rejected: {rel_path}")
    return rel_path.replace("\\", "/")
