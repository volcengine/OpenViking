# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Guards for local-path handling on the HTTP server."""

from __future__ import annotations

import re
from pathlib import Path

from openviking_cli.exceptions import PermissionDeniedError

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_REMOTE_SOURCE_PREFIXES = ("http://", "https://", "git@", "ssh://", "git://")


def is_remote_resource_source(source: str) -> bool:
    """Return True if *source* is a remotely fetchable resource location."""
    return source.startswith(_REMOTE_SOURCE_PREFIXES)


def looks_like_local_path(value: str) -> bool:
    """Return True for strings that clearly look like filesystem paths."""
    if not value or "\n" in value or "\r" in value:
        return False
    return (
        value.startswith(("/", "./", "../", "~/", ".\\", "..\\", "~\\"))
        or "/" in value
        or "\\" in value
        or bool(_WINDOWS_DRIVE_RE.match(value))
    )


def require_remote_resource_source(source: str) -> str:
    """Reject direct host-path resource ingestion over HTTP."""
    if not is_remote_resource_source(source):
        raise PermissionDeniedError(
            "HTTP server only accepts remote resource URLs or temp-uploaded files; "
            "direct host filesystem paths are not allowed."
        )
    return source


def deny_direct_local_skill_input(value: str) -> None:
    """Reject obvious local filesystem paths for skill uploads over HTTP."""
    if looks_like_local_path(value):
        raise PermissionDeniedError(
            "HTTP server only accepts raw skill content or temp-uploaded files; "
            "direct host filesystem paths are not allowed."
        )


def resolve_uploaded_temp_file_id(temp_file_id: str, upload_temp_dir: Path) -> str:
    """Resolve a temp upload id to a regular file under the server upload temp dir."""
    if not temp_file_id or temp_file_id in {".", ".."}:
        raise PermissionDeniedError(
            "HTTP server only accepts regular files from the upload temp directory."
        )

    raw_name = Path(temp_file_id)
    if raw_name.name != temp_file_id or "/" in temp_file_id or "\\" in temp_file_id:
        raise PermissionDeniedError(
            "HTTP server only accepts temp_file_id values issued from the upload temp directory."
        )

    raw_path = upload_temp_dir / temp_file_id
    if raw_path.is_symlink():
        raise PermissionDeniedError(
            "HTTP server only accepts regular files from the upload temp directory."
        )

    try:
        resolved_path = raw_path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise PermissionDeniedError(
            "HTTP server only accepts regular files from the upload temp directory."
        ) from exc

    upload_root = upload_temp_dir.resolve()
    try:
        resolved_path.relative_to(upload_root)
    except ValueError as exc:
        raise PermissionDeniedError(
            "HTTP server only accepts temp_file_id values issued from the upload temp directory."
        ) from exc

    if not resolved_path.is_file():
        raise PermissionDeniedError(
            "HTTP server only accepts regular files from the upload temp directory."
        )

    return str(resolved_path)
