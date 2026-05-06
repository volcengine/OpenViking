# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Guards for local-path handling on the HTTP server."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, Tuple

from openviking.utils.network_guard import ensure_public_remote_target
from openviking_cli.exceptions import PermissionDeniedError

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_REMOTE_SOURCE_PREFIXES = ("http://", "https://", "git@", "ssh://", "git://")

# Shape check for tfids minted by the server / accepted on signed upload paths.
# Prefix is alnum (server-generated UUIDs). Extension allows any character that
# isn't a path separator — original filenames like ``report.my-file`` or
# ``notes.中文`` produce non-alnum extensions and must round-trip.
TEMP_FILE_ID_RE = re.compile(r"^upload_[a-zA-Z0-9]+(\.[^/\\]+)?$")


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
    try:
        if meta_path.exists():
            with open(meta_path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _is_safe_namespace_component(value: str) -> bool:
    """Reject path-traversal-shaped account/user identifiers."""
    return bool(value) and value not in {".", ".."} and "/" not in value and "\\" not in value


def resolve_uploaded_temp_file_id(
    temp_file_id: str,
    upload_temp_dir: Path,
    *,
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Resolve a temp upload id to a regular file under the server upload temp dir.

    Looks up under ``{upload_temp_dir}/{account_id}/{user_id}/{temp_file_id}`` when both
    namespace components are provided, then falls back to the flat layout
    ``{upload_temp_dir}/{temp_file_id}`` (the legacy CLI ``temp_upload`` writes here).

    Returns:
        Tuple of (resolved_file_path, original_filename)
        original_filename is None if no meta file exists.
    """
    if not temp_file_id or temp_file_id in {".", ".."}:
        raise PermissionDeniedError(
            "HTTP server only accepts regular files from the upload temp directory."
        )

    raw_name = Path(temp_file_id)
    if raw_name.name != temp_file_id or "/" in temp_file_id or "\\" in temp_file_id:
        raise PermissionDeniedError(
            "HTTP server only accepts temp_file_id values issued from the upload temp directory."
        )

    upload_root = upload_temp_dir.resolve()

    candidates: list[Path] = []
    if account_id and user_id:
        if not (_is_safe_namespace_component(account_id) and _is_safe_namespace_component(user_id)):
            raise PermissionDeniedError(
                "HTTP server only accepts temp_file_id values issued from the upload temp directory."
            )
        candidates.append(upload_temp_dir / account_id / user_id / temp_file_id)
    candidates.append(upload_temp_dir / temp_file_id)

    last_exc: Optional[Exception] = None
    for raw_path in candidates:
        if raw_path.is_symlink():
            raise PermissionDeniedError(
                "HTTP server only accepts regular files from the upload temp directory."
            )
        try:
            resolved_path = raw_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            last_exc = exc
            continue
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

        meta_path = raw_path.parent / f"{temp_file_id}.ov_upload.meta"
        meta = _read_upload_meta(meta_path)
        original_filename = meta.get("original_filename") if meta else None
        return (str(resolved_path), original_filename)

    raise PermissionDeniedError(
        "HTTP server only accepts regular files from the upload temp directory."
    ) from last_exc
