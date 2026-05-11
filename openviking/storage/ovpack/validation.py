# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OVPack archive/member validation helpers."""

from __future__ import annotations

import json
import zipfile
from typing import Any

from openviking.storage.ovpack.format import (
    OVPACK_DENSE_PATH,
    OVPACK_INDEX_RECORDS_PATH,
    OVPACK_MANIFEST_ZIP_LEAF,
    get_viking_rel_path_from_zip,
    internal_zip_path,
    is_content_zip_path,
    is_internal_zip_path,
    is_manifest_zip_path,
    join_uri,
    manifest_content_sha256,
    normalize_sha256,
    sha256_hex,
    validate_ovpack_member_path,
)
from openviking.storage.ovpack.manifest import (
    manifest_dense_info,
    manifest_entries_by_path,
    manifest_index_records_info,
)
from openviking.storage.ovpack.policy import validate_import_target_uri
from openviking_cli.exceptions import InvalidArgumentError


def base_name_from_entries(infolist: list[zipfile.ZipInfo]) -> str:
    for info in infolist:
        filename = info.filename
        if filename:
            base_name = filename.replace("\\", "/").split("/")[0]
            if base_name:
                return base_name
    raise ValueError("Could not determine root directory name from ovpack")


def record_dense_ref(record: dict[str, Any]) -> dict[str, int] | None:
    vector = record.get("vector")
    if not isinstance(vector, dict):
        return None
    dense = vector.get("dense")
    if not isinstance(dense, dict):
        return None
    offset = dense.get("offset")
    dimensions = dense.get("dimensions")
    if not isinstance(offset, int) or not isinstance(dimensions, int):
        return None
    return {"offset": offset, "dimensions": dimensions}


def dense_record_count(index_records: list[dict[str, Any]]) -> int:
    return sum(1 for record in index_records if record_dense_ref(record) is not None)


def _zip_file_members_by_path(
    infolist: list[zipfile.ZipInfo], base_name: str
) -> dict[str, tuple[zipfile.ZipInfo, str]]:
    files: dict[str, tuple[zipfile.ZipInfo, str]] = {}
    for info in infolist:
        zip_path = info.filename
        if not zip_path:
            continue
        safe_zip_path = validate_ovpack_member_path(zip_path, base_name)
        if (
            is_manifest_zip_path(safe_zip_path, base_name)
            or is_internal_zip_path(safe_zip_path, base_name)
            or safe_zip_path.endswith("/")
        ):
            continue
        if not is_content_zip_path(safe_zip_path, base_name):
            raise InvalidArgumentError(
                "Unexpected ovpack member outside files directory",
                details={"path": safe_zip_path},
            )

        rel_path = get_viking_rel_path_from_zip(safe_zip_path)
        if rel_path in files:
            raise InvalidArgumentError(
                "Duplicate ovpack file entry",
                details={"path": rel_path},
            )
        files[rel_path] = (info, safe_zip_path)
    return files


def _zip_directory_members_by_path(infolist: list[zipfile.ZipInfo], base_name: str) -> set[str]:
    directories: set[str] = set()
    for info in infolist:
        zip_path = info.filename
        if not zip_path:
            continue
        safe_zip_path = validate_ovpack_member_path(zip_path, base_name)
        stripped_zip_path = safe_zip_path.rstrip("/")
        if is_internal_zip_path(stripped_zip_path, base_name):
            continue
        if stripped_zip_path == base_name:
            continue
        if not safe_zip_path.endswith("/"):
            continue
        if not is_content_zip_path(stripped_zip_path, base_name):
            raise InvalidArgumentError(
                "Unexpected ovpack directory outside files directory",
                details={"path": safe_zip_path},
            )

        rel_path = get_viking_rel_path_from_zip(stripped_zip_path)
        if rel_path in directories:
            raise InvalidArgumentError(
                "Duplicate ovpack directory entry",
                details={"path": rel_path},
            )
        directories.add(rel_path)
    return directories


def _parse_index_records(raw: bytes, expected_count: int, path: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidArgumentError("Invalid ovpack index records encoding") from exc

    lines = text.splitlines() if text else []
    if len(lines) != expected_count:
        raise InvalidArgumentError(
            "ovpack index record count does not match manifest",
            details={"path": path, "expected": expected_count, "actual": len(lines)},
        )

    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InvalidArgumentError(
                "Invalid JSON in ovpack index record",
                details={"path": path, "line": index + 1},
            ) from exc
        if not isinstance(record, dict):
            raise InvalidArgumentError(
                "Invalid ovpack index record",
                details={"path": path, "line": index + 1},
            )
        records.append(record)
    return records


def _validate_index_record(
    record: dict[str, Any],
    manifest_entries: dict[str, dict[str, Any]],
    index: int,
) -> None:
    record_id = record.get("record_id")
    rel_path = record.get("path")
    kind = record.get("kind")
    level = record.get("level")
    if not isinstance(record_id, str) or not record_id:
        raise InvalidArgumentError(
            "Invalid ovpack index record id",
            details={"index": index},
        )
    if not isinstance(rel_path, str) or rel_path not in manifest_entries:
        raise InvalidArgumentError(
            "Invalid ovpack index record path",
            details={"index": index, "path": rel_path},
        )
    if kind != manifest_entries[rel_path].get("kind"):
        raise InvalidArgumentError(
            "ovpack index record kind does not match manifest",
            details={"index": index, "path": rel_path, "kind": kind},
        )
    if not isinstance(level, int) or isinstance(level, bool):
        raise InvalidArgumentError(
            "Invalid ovpack index record level",
            details={"index": index, "path": rel_path, "level": level},
        )

    text = record.get("text")
    if text is not None and not isinstance(text, str):
        raise InvalidArgumentError(
            "Invalid ovpack index record text",
            details={"index": index, "path": rel_path},
        )
    scalars = record.get("scalars")
    if scalars is not None and not isinstance(scalars, dict):
        raise InvalidArgumentError(
            "Invalid ovpack index record scalars",
            details={"index": index, "path": rel_path},
        )


def _validate_dense_references(
    dense_info: dict[str, Any] | None,
    dense_data: bytes | None,
    index_records: list[dict[str, Any]],
) -> None:
    refs: list[tuple[int, int]] = []
    for index, record in enumerate(index_records):
        vector = record.get("vector")
        if vector is None:
            continue
        if not isinstance(vector, dict):
            raise InvalidArgumentError(
                "Invalid ovpack index record vector",
                details={"index": index, "path": record.get("path")},
            )
        dense = vector.get("dense")
        if not isinstance(dense, dict):
            raise InvalidArgumentError(
                "Invalid ovpack index record dense vector reference",
                details={"index": index, "path": record.get("path")},
            )
        offset = dense.get("offset")
        dimensions = dense.get("dimensions")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise InvalidArgumentError(
                "Invalid ovpack dense vector offset",
                details={"index": index, "path": record.get("path"), "offset": offset},
            )
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions <= 0:
            raise InvalidArgumentError(
                "Invalid ovpack dense vector dimensions",
                details={"index": index, "path": record.get("path"), "dimensions": dimensions},
            )
        refs.append((offset, dimensions))

    if not refs:
        if dense_info is not None:
            raise InvalidArgumentError("ovpack dense vector file has no record references")
        return
    if dense_info is None or dense_data is None:
        raise InvalidArgumentError(
            "ovpack index references dense vectors but dense data is missing"
        )

    refs.sort()
    expected_offset = 0
    for offset, dimensions in refs:
        if offset != expected_offset:
            raise InvalidArgumentError(
                "ovpack dense vector offsets are not contiguous",
                details={"expected": expected_offset, "actual": offset},
            )
        if dimensions != dense_info["dimensions"]:
            raise InvalidArgumentError(
                "ovpack dense vector dimensions do not match manifest",
                details={"expected": dense_info["dimensions"], "actual": dimensions},
            )
        expected_offset += dimensions

    if dense_info["count"] != len(refs):
        raise InvalidArgumentError(
            "ovpack dense vector count does not match index records",
            details={"expected": dense_info["count"], "actual": len(refs)},
        )
    expected_size = expected_offset * 4
    if len(dense_data) != expected_size:
        raise InvalidArgumentError(
            "ovpack dense vector byte size does not match manifest",
            details={"expected": expected_size, "actual": len(dense_data)},
        )


def _validate_index_content(
    zf: zipfile.ZipFile,
    manifest: dict[str, Any],
    base_name: str,
    manifest_entries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    records_info = manifest_index_records_info(manifest)
    records_zip_path = internal_zip_path(base_name, OVPACK_INDEX_RECORDS_PATH)
    try:
        records_data = zf.read(records_zip_path)
    except KeyError as exc:
        raise InvalidArgumentError(
            "Missing ovpack index records",
            details={"path": OVPACK_INDEX_RECORDS_PATH},
        ) from exc
    expected_records_sha = normalize_sha256(
        records_info.get("sha256"),
        field="index.records.sha256",
    )
    actual_records_sha = sha256_hex(records_data)
    if actual_records_sha != expected_records_sha:
        raise InvalidArgumentError(
            "ovpack index records sha256 mismatch",
            details={"expected": expected_records_sha, "actual": actual_records_sha},
        )

    index_records = _parse_index_records(
        records_data,
        records_info["count"],
        OVPACK_INDEX_RECORDS_PATH,
    )
    seen_record_ids: set[str] = set()
    for index, record in enumerate(index_records):
        _validate_index_record(record, manifest_entries, index)
        record_id = record["record_id"]
        if record_id in seen_record_ids:
            raise InvalidArgumentError(
                "Duplicate ovpack index record id",
                details={"record_id": record_id},
            )
        seen_record_ids.add(record_id)

    dense_info = manifest_dense_info(manifest)
    dense_data = None
    if dense_info is not None:
        dense_zip_path = internal_zip_path(base_name, OVPACK_DENSE_PATH)
        try:
            dense_data = zf.read(dense_zip_path)
        except KeyError as exc:
            raise InvalidArgumentError(
                "Missing ovpack dense vector data",
                details={"path": OVPACK_DENSE_PATH},
            ) from exc
        expected_dense_sha = normalize_sha256(
            dense_info.get("sha256"),
            field="index.dense.sha256",
        )
        actual_dense_sha = sha256_hex(dense_data)
        if actual_dense_sha != expected_dense_sha:
            raise InvalidArgumentError(
                "ovpack dense vector sha256 mismatch",
                details={"expected": expected_dense_sha, "actual": actual_dense_sha},
            )

    _validate_dense_references(dense_info, dense_data, index_records)
    return index_records


def _validate_internal_members(
    infolist: list[zipfile.ZipInfo],
    base_name: str,
    manifest: dict[str, Any],
) -> None:
    expected_files = {
        internal_zip_path(base_name, OVPACK_MANIFEST_ZIP_LEAF),
        internal_zip_path(base_name, OVPACK_INDEX_RECORDS_PATH),
    }
    if manifest_dense_info(manifest) is not None:
        expected_files.add(internal_zip_path(base_name, OVPACK_DENSE_PATH))

    actual_files: set[str] = set()
    for info in infolist:
        safe_zip_path = validate_ovpack_member_path(info.filename, base_name)
        if not is_internal_zip_path(safe_zip_path, base_name) or safe_zip_path.endswith("/"):
            continue
        actual_files.add(safe_zip_path)

    missing = sorted(expected_files - actual_files)
    unexpected = sorted(actual_files - expected_files)
    if missing or unexpected:
        raise InvalidArgumentError(
            "ovpack internal entries do not match manifest",
            details={
                "missing_files": [path.split("/", 1)[1] for path in missing],
                "unexpected_files": [path.split("/", 1)[1] for path in unexpected],
            },
        )


def validate_manifest_content(
    zf: zipfile.ZipFile,
    manifest: dict[str, Any],
    infolist: list[zipfile.ZipInfo],
    base_name: str,
) -> list[dict[str, Any]]:
    if "entries" not in manifest:
        raise InvalidArgumentError(
            "Missing ovpack manifest entries",
            details={"field": "entries"},
        )

    manifest_entries = manifest_entries_by_path(manifest)
    manifest_files = {
        rel_path: entry
        for rel_path, entry in manifest_entries.items()
        if entry.get("kind") == "file"
    }
    manifest_directories = {
        rel_path for rel_path, entry in manifest_entries.items() if entry.get("kind") == "directory"
    }
    zip_files = _zip_file_members_by_path(infolist, base_name)
    zip_directories = _zip_directory_members_by_path(infolist, base_name)
    _validate_internal_members(infolist, base_name, manifest)

    missing_files = sorted(set(manifest_files) - set(zip_files))
    unexpected_files = sorted(set(zip_files) - set(manifest_files))
    missing_directories = sorted(manifest_directories - zip_directories)
    unexpected_directories = sorted(zip_directories - manifest_directories)
    if missing_files or unexpected_files or missing_directories or unexpected_directories:
        raise InvalidArgumentError(
            "ovpack entries do not match manifest",
            details={
                "missing_files": missing_files,
                "unexpected_files": unexpected_files,
                "missing_directories": missing_directories,
                "unexpected_directories": unexpected_directories,
            },
        )

    expected_content_sha256 = manifest.get("content_sha256")
    if expected_content_sha256 is None:
        raise InvalidArgumentError(
            "Missing ovpack manifest content_sha256",
            details={"field": "content_sha256"},
        )
    expected_content_sha256 = normalize_sha256(
        expected_content_sha256,
        field="content_sha256",
    )
    actual_content_sha256 = manifest_content_sha256(manifest_files)
    if actual_content_sha256 != expected_content_sha256:
        raise InvalidArgumentError(
            "ovpack manifest content_sha256 mismatch",
            details={
                "expected": expected_content_sha256,
                "actual": actual_content_sha256,
            },
        )

    for rel_path, (_, safe_zip_path) in sorted(zip_files.items()):
        entry = manifest_files[rel_path]
        data = zf.read(safe_zip_path)

        expected_size = entry.get("size")
        if expected_size is not None:
            if (
                not isinstance(expected_size, int)
                or isinstance(expected_size, bool)
                or expected_size < 0
            ):
                raise InvalidArgumentError(
                    "Invalid ovpack manifest file size",
                    details={"path": rel_path, "size": expected_size},
                )
            if len(data) != expected_size:
                raise InvalidArgumentError(
                    "ovpack file size does not match manifest",
                    details={
                        "path": rel_path,
                        "expected": expected_size,
                        "actual": len(data),
                    },
                )

        expected_sha256 = entry.get("sha256")
        if expected_sha256 is not None:
            expected_sha256 = normalize_sha256(
                expected_sha256,
                field="sha256",
                path=rel_path,
            )
            actual_sha256 = sha256_hex(data)
            if actual_sha256 != expected_sha256:
                raise InvalidArgumentError(
                    "ovpack file sha256 does not match manifest",
                    details={
                        "path": rel_path,
                        "expected": expected_sha256,
                        "actual": actual_sha256,
                    },
                )

    return _validate_index_content(zf, manifest, base_name, manifest_entries)


def validated_import_members(
    infolist: list[zipfile.ZipInfo], base_name: str, root_uri: str
) -> list[tuple[zipfile.ZipInfo, str, str, str]]:
    members: list[tuple[zipfile.ZipInfo, str, str, str]] = []
    for info in infolist:
        zip_path = info.filename
        if not zip_path:
            continue

        safe_zip_path = validate_ovpack_member_path(zip_path, base_name)
        stripped_zip_path = safe_zip_path.rstrip("/")
        if is_manifest_zip_path(safe_zip_path, base_name):
            members.append((info, safe_zip_path, "manifest", ""))
            continue
        if is_internal_zip_path(stripped_zip_path, base_name):
            members.append((info, safe_zip_path, "internal", ""))
            continue
        if stripped_zip_path == base_name:
            continue
        if not is_content_zip_path(stripped_zip_path, base_name):
            raise InvalidArgumentError(
                "Unexpected ovpack member outside files directory",
                details={"path": safe_zip_path},
            )

        kind = "directory" if safe_zip_path.endswith("/") else "file"
        rel_path = get_viking_rel_path_from_zip(
            safe_zip_path.rstrip("/") if kind == "directory" else safe_zip_path
        )
        if root_uri == "viking://" and rel_path == "":
            members.append((info, safe_zip_path, kind, rel_path))
            continue
        target_uri = join_uri(root_uri, rel_path)
        validate_import_target_uri(target_uri)
        members.append((info, safe_zip_path, kind, rel_path))

    return members
