# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Guards for local-path handling on the HTTP server."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from openviking.utils.network_guard import ensure_public_remote_target
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
    ensure_public_remote_target(source)
    return source


def deny_direct_local_skill_input(value: str) -> None:
    """Reject obvious local filesystem paths for skill uploads over HTTP."""
    if looks_like_local_path(value):
        raise PermissionDeniedError(
            "HTTP server only accepts raw skill content or temp-uploaded files; "
            "direct host filesystem paths are not allowed."
        )


def _read_upload_meta(meta_path: Path) -> Optional[dict]:
    """Read upload metadata file if it exists."""
    import json

    try:
        if meta_path.exists():
            with open(meta_path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return None
