# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OVPack manifest parsing and structural validation helpers."""

from __future__ import annotations

import json
import zipfile
from typing import Any

from openviking.core.identifiers import validate_user_id
from openviking.core.namespace import owner_fields_for_uri, uri_leaf_name
from openviking.storage.ovpack.format import (
    OVPACK_DENSE_PATH,
    OVPACK_FORMAT_VERSION,
    OVPACK_INDEX_RECORDS_PATH,
    OVPACK_KIND,
    OVPACK_MANIFEST_ZIP_LEAF,
    join_uri,
    normalize_sha256,
    validate_ovpack_rel_path,
)
from openviking.storage.ovpack.policy import manifest_root_uri
from openviking_cli.exceptions import InvalidArgumentError


def invalid_manifest(message: str, manifest_path: str, **details: Any) -> InvalidArgumentError:
    return InvalidArgumentError(
        message,
        details={"manifest_path": manifest_path, **details},
    )


def read_manifest(zf: zipfile.ZipFile, base_name: str) -> dict[str, Any]:
    manifest_path = f"{base_name}/{OVPACK_MANIFEST_ZIP_LEAF}"
    try:
        raw = zf.read(manifest_path)
    except KeyError:
        raise invalid_manifest(
            "Missing ovpack manifest",
            manifest_path,
            hint="Re-export this package with the current OVPack format before importing.",
        )

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise invalid_manifest(
            "Invalid JSON in ovpack manifest",
            manifest_path,
            reason=str(exc),
        ) from exc
    if not isinstance(manifest, dict):
        raise invalid_manifest(
            "Invalid ovpack manifest",
            manifest_path,
            actual_type=type(manifest).__name__,
        )

    version = manifest.get("format_version")
    if version is None:
        raise invalid_manifest(
            "Missing ovpack format_version",
            manifest_path,
            field="format_version",
        )
    try:
        version_int = int(version)
    except (TypeError, ValueError) as exc:
        raise invalid_manifest(
            f"Invalid ovpack format_version {version!r}",
            manifest_path,
            field="format_version",
            value=version,
        ) from exc
    if version_int < 1:
        raise invalid_manifest(
            f"Invalid ovpack format_version {version!r}",
            manifest_path,
            field="format_version",
            value=version,
        )
    if version_int != OVPACK_FORMAT_VERSION:
        raise invalid_manifest(
            f"Unsupported ovpack format_version {version}; "
            f"this OpenViking requires {OVPACK_FORMAT_VERSION}",
            manifest_path,
            format_version=version_int,
            supported_format_version=OVPACK_FORMAT_VERSION,
        )
    if manifest.get("kind") != OVPACK_KIND:
        raise invalid_manifest(
            "Invalid ovpack manifest kind",
            manifest_path,
            expected=OVPACK_KIND,
            actual=manifest.get("kind"),
        )
    return manifest


def validate_manifest_root_matches_zip(manifest: dict[str, Any], base_name: str) -> None:
    root = manifest.get("root")
    if not isinstance(root, dict):
        raise InvalidArgumentError("Missing ovpack manifest root")

    root_name = root.get("name")
    if not isinstance(root_name, str) or not root_name:
        raise InvalidArgumentError(
            "Missing ovpack manifest root name",
            details={"field": "root.name"},
        )
    if root_name != base_name:
        raise InvalidArgumentError(
            "ovpack manifest root name does not match zip root",
            details={"manifest_root_name": root_name, "zip_root": base_name},
        )

    root_uri = manifest_root_uri(manifest)
    if root_uri and root_uri != "viking://" and uri_leaf_name(root_uri) != root_name:
        raise InvalidArgumentError(
            "ovpack manifest root name does not match root uri",
            details={"manifest_root_name": root_name, "root_uri": root_uri},
        )

    expected_user_id = owner_fields_for_uri(root_uri).get("owner_user_id")
    declared_user_id = root.get("user_id")
    if declared_user_id is not None and declared_user_id != expected_user_id:
        raise InvalidArgumentError(
            "ovpack manifest root user_id does not match root uri",
            details={"user_id": declared_user_id, "root_uri": root_uri},
        )
    if expected_user_id:
        root["user_id"] = expected_user_id


def _entry_user_id(manifest: dict[str, Any], rel_path: str) -> str | None:
    root_uri = manifest_root_uri(manifest)
    if not root_uri:
        return None
    target_uri = join_uri(root_uri, rel_path) if rel_path else root_uri
    return owner_fields_for_uri(target_uri).get("owner_user_id")


def manifest_entry_target_uri(
    root_uri: str,
    rel_path: str,
    entry: dict[str, Any],
) -> str:
    """Map one validated manifest entry to its canonical restore target URI."""
    parts = rel_path.split("/") if rel_path else []
    if root_uri != "viking://" or not parts or parts[0] != "user":
        return join_uri(root_uri, rel_path)
    if len(parts) == 1:
        return "viking://user"

    user_id = entry.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise InvalidArgumentError(
            "Missing ovpack manifest user_id for user data",
            details={"path": rel_path},
        )
    suffix = "/".join(parts[2:])
    user_root = f"viking://user/{user_id}"
    return join_uri(user_root, suffix) if suffix else user_root


def manifest_entries_by_path(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(manifest, dict) or "entries" not in manifest:
        return {}

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise InvalidArgumentError("Invalid ovpack manifest: entries must be a list")

    by_path: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise InvalidArgumentError(
                "Invalid ovpack manifest entry",
                details={"index": index},
            )

        rel_path = entry.get("path")
        kind = entry.get("kind")
        if not isinstance(rel_path, str):
            raise InvalidArgumentError(
                "Invalid ovpack manifest entry path",
                details={"index": index},
            )
        validate_ovpack_rel_path(rel_path)
        if kind not in {"directory", "file"}:
            raise InvalidArgumentError(
                "Invalid ovpack manifest entry kind",
                details={"path": rel_path, "kind": kind},
            )
        if rel_path in by_path:
            raise InvalidArgumentError(
                "Duplicate ovpack manifest entry",
                details={"path": rel_path},
            )
        expected_user_id = _entry_user_id(manifest, rel_path)
        declared_user_id = entry.get("user_id")
        if declared_user_id is not None and declared_user_id != expected_user_id:
            raise InvalidArgumentError(
                "ovpack manifest entry user_id does not match path",
                details={"path": rel_path, "user_id": declared_user_id},
            )
        if expected_user_id:
            validation_error = validate_user_id(expected_user_id)
            if validation_error:
                raise InvalidArgumentError(
                    f"Invalid ovpack manifest user_id: {validation_error}",
                    details={"path": rel_path, "user_id": expected_user_id},
                )
            entry["user_id"] = expected_user_id
        by_path[rel_path] = entry

    return by_path


def manifest_index_records_info(manifest: dict[str, Any]) -> dict[str, Any]:
    index = manifest.get("index")
    if not isinstance(index, dict):
        raise InvalidArgumentError("Missing ovpack index", details={"field": "index"})

    records = index.get("records")
    if not isinstance(records, dict):
        raise InvalidArgumentError(
            "Missing ovpack index records",
            details={"field": "index.records"},
        )
    if records.get("path") != OVPACK_INDEX_RECORDS_PATH:
        raise InvalidArgumentError(
            "Invalid ovpack index records path",
            details={"path": records.get("path")},
        )
    count = records.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise InvalidArgumentError(
            "Invalid ovpack index records count",
            details={"count": count},
        )
    normalize_sha256(records.get("sha256"), field="index.records.sha256")
    return records


def manifest_dense_info(manifest: dict[str, Any]) -> dict[str, Any] | None:
    index = manifest.get("index")
    if not isinstance(index, dict):
        return None
    dense = index.get("dense")
    if dense is None:
        return None
    if not isinstance(dense, dict):
        raise InvalidArgumentError(
            "Invalid ovpack dense vector index",
            details={"field": "index.dense"},
        )
    if dense.get("path") != OVPACK_DENSE_PATH:
        raise InvalidArgumentError(
            "Invalid ovpack dense vector path",
            details={"path": dense.get("path")},
        )
    count = dense.get("count")
    dimensions = dense.get("dimensions")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise InvalidArgumentError(
            "Invalid ovpack dense vector count",
            details={"count": count},
        )
    if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions <= 0:
        raise InvalidArgumentError(
            "Invalid ovpack dense vector dimensions",
            details={"dimensions": dimensions},
        )
    if dense.get("dtype") != "float32" or dense.get("byte_order") != "little":
        raise InvalidArgumentError(
            "Unsupported ovpack dense vector encoding",
            details={
                "dtype": dense.get("dtype"),
                "byte_order": dense.get("byte_order"),
            },
        )
    normalize_sha256(dense.get("sha256"), field="index.dense.sha256")
    return dense
