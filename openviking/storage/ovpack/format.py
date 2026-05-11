# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Low-level OVPack format helpers.

This module contains only stateless helpers for names, ZIP paths, checksums,
and binary encodings. It does not know about VikingFS writes, vector stores, or
import/export conflict handling.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from typing import Any, Optional

from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.uri import VikingURI

OVPACK_FORMAT_VERSION = 2
OVPACK_KIND = "openviking.ovpack"
OVPACK_INTERNAL_DIR = "_ovpack"
OVPACK_FILES_DIR = "files"
OVPACK_MANIFEST_FILENAME = "manifest.json"
OVPACK_MANIFEST_ZIP_LEAF = f"{OVPACK_INTERNAL_DIR}/{OVPACK_MANIFEST_FILENAME}"
OVPACK_INDEX_RECORDS_FILENAME = "index_records.jsonl"
OVPACK_INDEX_RECORDS_PATH = f"{OVPACK_INTERNAL_DIR}/{OVPACK_INDEX_RECORDS_FILENAME}"
OVPACK_DENSE_FILENAME = "dense.f32"
OVPACK_DENSE_PATH = f"{OVPACK_INTERNAL_DIR}/{OVPACK_DENSE_FILENAME}"
OVPACK_ON_CONFLICT_VALUES = frozenset({"fail", "overwrite", "skip"})
OVPACK_VECTOR_MODE_VALUES = frozenset({"auto", "recompute", "require"})
OVPACK_BACKUP_NAME = "openviking-backup"
OVPACK_BACKUP_TYPE = "backup"

_UNSAFE_PATH_RE = re.compile(r"(^|[\\/])\.\.($|[\\/])")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def strip_uri_trailing_slash(uri: str) -> str:
    normalized = VikingURI(uri.strip()).uri
    return normalized if normalized == "viking://" else normalized.rstrip("/")


def join_uri(base_uri: str, rel_path: str) -> str:
    return VikingURI(strip_uri_trailing_slash(base_uri)).join(rel_path).uri


def leaf_name(uri_or_path: str) -> str:
    return uri_or_path.rstrip("/").split("/")[-1]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_manifest_zip_path(zip_path: str, base_name: str) -> bool:
    return zip_path == f"{base_name}/{OVPACK_MANIFEST_ZIP_LEAF}"


def is_internal_zip_path(zip_path: str, base_name: str) -> bool:
    return zip_path == f"{base_name}/{OVPACK_INTERNAL_DIR}" or zip_path.startswith(
        f"{base_name}/{OVPACK_INTERNAL_DIR}/"
    )


def is_content_zip_path(zip_path: str, base_name: str) -> bool:
    return zip_path == f"{base_name}/{OVPACK_FILES_DIR}" or zip_path.startswith(
        f"{base_name}/{OVPACK_FILES_DIR}/"
    )


def internal_zip_path(base_name: str, internal_path: str) -> str:
    return f"{base_name}/{internal_path}"


def validate_ovpack_rel_path(rel_path: str, *, allow_root: bool = True) -> None:
    """Validate a user relative path stored under the ZIP files/ namespace."""
    if rel_path == "":
        if allow_root:
            return
        raise InvalidArgumentError("Invalid ovpack relative path", details={"path": rel_path})
    if "\\" in rel_path or rel_path.startswith("/") or _DRIVE_RE.match(rel_path):
        raise InvalidArgumentError("Unsafe ovpack relative path", details={"path": rel_path})
    if _UNSAFE_PATH_RE.search(rel_path):
        raise InvalidArgumentError("Unsafe ovpack relative path", details={"path": rel_path})
    parts = rel_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidArgumentError("Unsafe ovpack relative path", details={"path": rel_path})


def validate_ovpack_member_path(zip_path: str, base_name: str) -> str:
    """Validate a zip member path for ovpack imports and reject unsafe entries."""
    if not zip_path:
        raise ValueError("Invalid ovpack entry: empty path")
    if "\\" in zip_path:
        raise ValueError(f"Unsafe ovpack entry path: {zip_path!r}")
    if zip_path.startswith("/"):
        raise ValueError(f"Unsafe ovpack entry path: {zip_path!r}")
    if _DRIVE_RE.match(zip_path):
        raise ValueError(f"Unsafe ovpack entry path: {zip_path!r}")
    if _UNSAFE_PATH_RE.search(zip_path):
        raise ValueError(f"Unsafe ovpack entry path: {zip_path!r}")

    parts = zip_path.split("/")
    if any(part == ".." for part in parts):
        raise ValueError(f"Unsafe ovpack entry path: {zip_path!r}")
    if not parts or parts[0] != base_name:
        raise ValueError(f"Invalid ovpack entry root: {zip_path!r}")

    return zip_path


def ensure_ovpack_extension(path: str) -> str:
    """Ensure path ends with .ovpack extension."""
    if not path.endswith(".ovpack"):
        return path + ".ovpack"
    return path


def ensure_dir_exists(path: str) -> None:
    """Ensure the parent directory of the given path exists."""
    out_dir = os.path.dirname(os.path.abspath(path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)


def get_ovpack_zip_path(base_name: str, rel_path: str) -> str:
    """Generate ZIP internal path from a Viking relative path."""
    validate_ovpack_rel_path(rel_path)
    if not rel_path:
        return f"{base_name}/{OVPACK_FILES_DIR}/"
    return f"{base_name}/{OVPACK_FILES_DIR}/{rel_path}"


def get_viking_rel_path_from_zip(zip_path: str) -> str:
    """Restore Viking relative path from ZIP path."""
    parts = zip_path.split("/")
    if len(parts) < 2 or parts[1] != OVPACK_FILES_DIR:
        raise ValueError(f"Invalid ovpack content path: {zip_path!r}")
    if len(parts) == 2:
        return ""
    rel_path = "/".join(parts[2:])
    validate_ovpack_rel_path(rel_path)
    return rel_path


def normalize_on_conflict(on_conflict: Optional[str]) -> str:
    if on_conflict is None:
        return "fail"
    if on_conflict not in OVPACK_ON_CONFLICT_VALUES:
        allowed = ", ".join(sorted(OVPACK_ON_CONFLICT_VALUES))
        raise InvalidArgumentError(
            f"Invalid on_conflict value: {on_conflict}. Must be one of: {allowed}"
        )
    return on_conflict


def normalize_vector_mode(vector_mode: Optional[str]) -> str:
    if vector_mode is None:
        return "auto"
    if vector_mode not in OVPACK_VECTOR_MODE_VALUES:
        allowed = ", ".join(sorted(OVPACK_VECTOR_MODE_VALUES))
        raise InvalidArgumentError(
            f"Invalid vector_mode value: {vector_mode}. Must be one of: {allowed}"
        )
    return vector_mode


def normalize_sha256(value: Any, *, field: str, path: str | None = None) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        details = {"field": field}
        if path is not None:
            details["path"] = path
        raise InvalidArgumentError(f"Invalid ovpack manifest {field}", details=details)
    return value.lower()


def manifest_content_sha256(file_entries_by_path: dict[str, dict[str, Any]]) -> str:
    content_entries: list[dict[str, Any]] = []
    for rel_path, entry in sorted(file_entries_by_path.items()):
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise InvalidArgumentError(
                "Invalid ovpack manifest file size",
                details={"path": rel_path, "size": size},
            )
        content_entries.append(
            {
                "path": rel_path,
                "size": size,
                "sha256": normalize_sha256(entry.get("sha256"), field="sha256", path=rel_path),
            }
        )

    payload = json.dumps(
        content_entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_hex(payload)


def jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    if not records:
        return b""
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def dense_values_bytes(values: list[float]) -> bytes:
    if not values:
        return b""
    return struct.pack(f"<{len(values)}f", *values)
