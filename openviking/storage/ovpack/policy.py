# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OVPack URI scope and import/restore policy helpers."""

from __future__ import annotations

from typing import Any

from openviking.core.namespace import uri_depth, uri_parts
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.storage.ovpack.format import (
    OVPACK_BACKUP_TYPE,
    join_uri,
    leaf_name,
    strip_uri_trailing_slash,
    validate_ovpack_rel_path,
)
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.uri import VikingURI

PUBLIC_SCOPES = ("resources", "user", "agent", "session")
IMPORTABLE_SCOPES = frozenset(PUBLIC_SCOPES)
STRUCTURED_IMPORT_SCOPES = frozenset({"user", "agent", "session"})
EXCLUDED_FILENAMES = frozenset({".relations.json"})
NON_VECTOR_SCOPES = frozenset({"session"})


def is_excluded_rel_path(rel_path: str) -> bool:
    return leaf_name(rel_path) in EXCLUDED_FILENAMES


def validate_ovpack_user_rel_path(rel_path: str, *, operation: str) -> None:
    try:
        validate_ovpack_rel_path(rel_path)
    except InvalidArgumentError as exc:
        raise InvalidArgumentError(
            f"cannot {operation} unsafe ovpack path",
            details={"path": rel_path},
        ) from exc


def _scope_relative_path(uri: str) -> str:
    parts = uri_parts(uri)
    if len(parts) <= 1:
        return ""
    return "/".join(parts[1:])


def validate_public_scope(uri: str, *, operation: str, allow_root: bool = False) -> None:
    parsed = VikingURI(uri)
    if parsed.uri == "viking://":
        if allow_root:
            return
        raise InvalidArgumentError(f"ovpack {operation} is not supported for root URI")
    if parsed.scope not in IMPORTABLE_SCOPES:
        raise InvalidArgumentError(f"ovpack {operation} is not supported for scope: {parsed.scope}")


def validate_import_target_uri(uri: str) -> None:
    """Enforce the same target-policy boundary as direct content writes."""
    validate_public_scope(uri, operation="import")
    validate_ovpack_user_rel_path(_scope_relative_path(uri), operation="import")
    name = leaf_name(uri)
    if name in EXCLUDED_FILENAMES:
        raise InvalidArgumentError(f"cannot import internal ovpack file: {uri}")
    if is_watch_task_control_uri(uri):
        raise InvalidArgumentError(f"cannot import watch task control file: {uri}")


def validate_export_source_uri(uri: str) -> None:
    validate_public_scope(uri, operation="export")
    validate_ovpack_user_rel_path(_scope_relative_path(uri), operation="export")
    name = leaf_name(uri)
    if name in EXCLUDED_FILENAMES:
        raise InvalidArgumentError(f"cannot export internal ovpack file: {uri}")
    if is_watch_task_control_uri(uri):
        raise InvalidArgumentError(f"cannot export watch task control file: {uri}")


def manifest_root_uri(manifest: dict[str, Any]) -> str:
    root = manifest.get("root")
    if not isinstance(root, dict):
        return ""
    uri = root.get("uri")
    if isinstance(uri, str):
        try:
            return strip_uri_trailing_slash(uri)
        except Exception:
            return uri.rstrip("/")
    return ""


def is_backup_package(manifest: dict[str, Any]) -> bool:
    root = manifest.get("root")
    return (
        isinstance(root, dict)
        and root.get("package_type") == OVPACK_BACKUP_TYPE
        and manifest_root_uri(manifest) == "viking://"
    )


def is_top_level_scope_package(manifest: dict[str, Any]) -> bool:
    return manifest_root_uri(manifest) in {f"viking://{scope}" for scope in IMPORTABLE_SCOPES}


def resolve_import_root_uri(parent: str, base_name: str, manifest: dict[str, Any]) -> str:
    if is_backup_package(manifest):
        raise InvalidArgumentError(
            "Backup ovpack packages must be restored with ov restore or the restore API",
            details={"root": base_name, "parent": parent},
        )

    if parent == "viking://":
        if not is_top_level_scope_package(manifest):
            raise InvalidArgumentError(
                "Only top-level scope ovpack packages can be imported to viking://",
                details={"root": base_name},
            )
        return manifest_root_uri(manifest)

    if is_top_level_scope_package(manifest):
        raise InvalidArgumentError(
            "Top-level scope ovpack packages must be imported to viking://",
            details={"root": base_name, "parent": parent},
        )
    return join_uri(parent, base_name)


def _parse_import_uri(uri: str, *, field: str) -> VikingURI:
    if not uri:
        raise InvalidArgumentError(f"Missing ovpack {field}")
    try:
        return VikingURI(uri)
    except ValueError as exc:
        raise InvalidArgumentError(f"Invalid ovpack {field}", details={field: uri}) from exc


def validate_import_scope_compatibility(manifest: dict[str, Any], target_root_uri: str) -> None:
    source_root_uri = manifest_root_uri(manifest)
    source = _parse_import_uri(source_root_uri, field="manifest root uri")
    target = _parse_import_uri(target_root_uri, field="target root uri")

    if source.scope not in IMPORTABLE_SCOPES:
        raise InvalidArgumentError(
            "ovpack import is not supported for source scope",
            details={"source_scope": source.scope},
        )
    if source.scope != target.scope:
        raise InvalidArgumentError(
            "ovpack source scope does not match target scope",
            details={
                "source_scope": source.scope,
                "target_scope": target.scope,
            },
        )
    if source.scope in STRUCTURED_IMPORT_SCOPES and uri_depth(source.uri) != uri_depth(target.uri):
        raise InvalidArgumentError(
            "ovpack source path is incompatible with target path",
            details={"source": source_root_uri, "target": target_root_uri},
        )


def backup_scopes_from_manifest(
    manifest: dict[str, Any], manifest_entries: dict[str, dict[str, Any]]
) -> tuple[str, ...]:
    roots = {rel_path.split("/", 1)[0] for rel_path in manifest_entries if rel_path}
    unexpected = sorted(root for root in roots if root not in IMPORTABLE_SCOPES)
    if unexpected:
        raise InvalidArgumentError(
            "Backup ovpack contains unsupported roots",
            details={"roots": unexpected},
        )

    directory_scope_roots = {
        rel_path
        for rel_path, entry in manifest_entries.items()
        if rel_path in IMPORTABLE_SCOPES and entry.get("kind") == "directory"
    }
    missing_scope_directories = sorted(roots - directory_scope_roots)
    if missing_scope_directories:
        raise InvalidArgumentError(
            "Backup ovpack scope roots must be directory entries",
            details={"missing_scope_directories": missing_scope_directories},
        )

    entry_scopes = tuple(scope for scope in PUBLIC_SCOPES if scope in directory_scope_roots)
    declared_scopes = manifest.get("scopes")
    if not isinstance(declared_scopes, list) or any(
        not isinstance(scope, str) for scope in declared_scopes
    ):
        raise InvalidArgumentError(
            "Invalid backup ovpack scopes",
            details={"field": "scopes"},
        )
    duplicate_scopes = sorted(
        scope for scope in set(declared_scopes) if declared_scopes.count(scope) > 1
    )
    invalid_scopes = sorted(scope for scope in declared_scopes if scope not in IMPORTABLE_SCOPES)
    if duplicate_scopes or invalid_scopes:
        raise InvalidArgumentError(
            "Invalid backup ovpack scopes",
            details={
                "duplicate_scopes": duplicate_scopes,
                "invalid_scopes": invalid_scopes,
            },
        )
    if set(declared_scopes) != set(entry_scopes):
        raise InvalidArgumentError(
            "Backup ovpack scopes do not match entries",
            details={
                "declared_scopes": declared_scopes,
                "entry_scopes": list(entry_scopes),
            },
        )
    return entry_scopes
