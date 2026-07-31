# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared helpers for safe user-supplied path handling."""

import re
from urllib.parse import unquote

from openviking_cli.utils.uri import VikingURI

_UNSAFE_REL_PATH_RE = re.compile(r"(^|[\\/])\.\.($|[\\/])")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _reject_encoded_path_escape(path: str) -> None:
    # Raw backslashes are accepted as Windows separators and normalized by
    # ``sanitize_relative_viking_path``. Decode only after splitting on both
    # raw separator forms so encoded separators cannot create a new segment.
    for segment in path.replace("\\", "/").split("/"):
        decoded = unquote(segment)
        if decoded != segment and (
            decoded in {".", ".."} or "/" in decoded or "\\" in decoded
        ):
            raise ValueError(f"Unsafe relative path rejected: {path}")


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
    _reject_encoded_path_escape(rel_path)
    return rel_path.replace("\\", "/")


def validate_safe_viking_uri_path(uri: str) -> str:
    """Reject ambiguous or traversal-bearing path syntax in a Viking URI."""
    normalized = VikingURI(uri.strip()).uri.rstrip("/")
    if "?" in normalized or "#" in normalized:
        raise ValueError(f"Unsafe Viking URI path rejected: {uri}")
    path = normalized[len(f"{VikingURI.SCHEME}://") :]
    if not path:
        return normalized
    safe_path = sanitize_relative_viking_path(path)
    if safe_path != path:
        raise ValueError(f"Unsafe Viking URI path rejected: {uri}")
    return normalized


def safe_join_viking_uri(base_uri: str, rel_path: str) -> str:
    """Join a Viking URI base with a sanitized relative child path."""
    safe_rel_path = sanitize_relative_viking_path(rel_path)
    return VikingURI(base_uri).join(safe_rel_path).uri
