# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OVPack export/import and backup/restore operations."""

import asyncio
import json
import os
import zipfile
from typing import Any, Optional

from openviking.core.namespace import context_type_for_uri, relative_uri_path
from openviking.server.identity import RequestContext
from openviking.storage.index_consistency import check_index_consistency
from openviking.storage.ovpack.format import (
    OVPACK_BACKUP_NAME,
    OVPACK_BACKUP_TYPE,
    OVPACK_FILES_DIR,
    OVPACK_INDEX_RECORDS_PATH,
    OVPACK_INTERNAL_DIR,
    OVPACK_MANIFEST_ZIP_LEAF,
    ensure_dir_exists,
    ensure_ovpack_extension,
    get_ovpack_zip_path,
    internal_zip_path,
    join_uri,
    jsonl_bytes,
    leaf_name,
    manifest_content_sha256,
    normalize_on_conflict,
    normalize_vector_mode,
    sha256_hex,
    strip_uri_trailing_slash,
)
from openviking.storage.ovpack.index import build_manifest, read_text_if_exists
from openviking.storage.ovpack.manifest import (
    manifest_entries_by_path,
    read_manifest,
    validate_manifest_root_matches_zip,
)
from openviking.storage.ovpack.policy import (
    NON_VECTOR_SCOPES,
    PUBLIC_SCOPES,
    backup_scopes_from_manifest,
    is_backup_package,
    is_excluded_rel_path,
    resolve_import_root_uri,
    validate_export_source_uri,
    validate_import_scope_compatibility,
    validate_import_target_uri,
    validate_ovpack_user_rel_path,
    validate_public_scope,
)
from openviking.storage.ovpack.validation import (
    base_name_from_entries,
    validate_manifest_content,
    validated_import_members,
)
from openviking.storage.ovpack.vectors import (
    build_dense_snapshot_manifest,
    choose_vector_restore_action,
    ensure_dense_snapshot_supported,
    read_dense_vectors,
    restore_vector_snapshot,
)
from openviking.utils.embedding_utils import vectorize_directory_meta, vectorize_file
from openviking_cli.exceptions import ConflictError, InvalidArgumentError, NotFoundError
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


def _index_records_by_level(
    index_records: list[dict[str, Any]], rel_path: str
) -> dict[int, dict[str, Any]]:
    by_level: dict[int, dict[str, Any]] = {}
    for record in index_records:
        if not isinstance(record, dict):
            continue
        if record.get("path") != rel_path:
            continue
        try:
            level = int(record.get("level", 2))
        except (TypeError, ValueError):
            continue
        by_level[level] = record
    return by_level


def _index_scalar_overrides(
    index_records: list[dict[str, Any]], rel_path: str
) -> dict[int, dict[str, Any]]:
    overrides: dict[int, dict[str, Any]] = {}
    for level, record in _index_records_by_level(index_records, rel_path).items():
        scalars = record.get("scalars")
        if isinstance(scalars, dict):
            overrides[level] = dict(scalars)
    return overrides


async def _root_exists(viking_fs, root_uri: str, ctx: RequestContext) -> bool:
    try:
        await viking_fs.ls(root_uri, ctx=ctx)
        return True
    except NotFoundError:
        return False
    except FileNotFoundError:
        return False


async def _ensure_parent_exists(viking_fs, parent: str, ctx: RequestContext) -> None:
    try:
        await viking_fs.stat(parent, ctx=ctx)
    except Exception:
        await viking_fs.mkdir(parent, ctx=ctx)


async def _remove_existing_root(viking_fs, root_uri: str, ctx: RequestContext) -> None:
    if not hasattr(viking_fs, "rm"):
        logger.warning(f"[ovpack] Cannot remove existing resource without rm(): {root_uri}")
        return
    try:
        await viking_fs.rm(root_uri, recursive=True, ctx=ctx)
    except NotFoundError:
        return
    except FileNotFoundError:
        return


async def _existing_scope_roots(
    viking_fs, scopes: tuple[str, ...], ctx: RequestContext
) -> list[str]:
    existing: list[str] = []
    for scope in scopes:
        scope_uri = f"viking://{scope}"
        if await _root_exists(viking_fs, scope_uri, ctx):
            existing.append(scope_uri)
    return existing


def _exportable_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exportable: list[dict[str, Any]] = []
    for entry in entries:
        rel_path = str(entry.get("rel_path") or "")
        validate_ovpack_user_rel_path(rel_path, operation="export")
        if not is_excluded_rel_path(rel_path):
            exportable.append(entry)
    return exportable


async def _enqueue_direct_vectorization(
    viking_fs,
    uri: str,
    ctx: RequestContext,
    index_records: Optional[list[dict[str, Any]]] = None,
    manifest_path_root_uri: Optional[str] = None,
) -> None:
    index_records = index_records or []
    manifest_path_root_uri = manifest_path_root_uri or uri
    entries = await viking_fs.tree(uri, node_limit=None, level_limit=None, ctx=ctx)
    dir_uris = {uri}
    file_entries: list[tuple[str, str, str, str]] = []
    for entry in entries:
        entry_uri = entry.get("uri")
        if not entry_uri:
            continue
        rel_path = entry.get("rel_path") or relative_uri_path(uri, entry_uri)
        manifest_rel_path = relative_uri_path(manifest_path_root_uri, entry_uri)
        if entry.get("isDir"):
            dir_uris.add(entry_uri)
            continue
        name = entry.get("name", "") or leaf_name(rel_path)
        if name.startswith("."):
            continue
        parent = VikingURI(entry_uri).parent
        if parent:
            file_entries.append((entry_uri, parent.uri, name, manifest_rel_path))

    async def index_dir(dir_uri: str) -> None:
        rel_path = relative_uri_path(manifest_path_root_uri, dir_uri)
        records_by_level = _index_records_by_level(index_records, rel_path)
        scalar_overrides = _index_scalar_overrides(index_records, rel_path)
        abstract = str(records_by_level.get(0, {}).get("text") or "")
        overview = str(records_by_level.get(1, {}).get("text") or "")

        if not abstract:
            abstract = await read_text_if_exists(viking_fs, f"{dir_uri}/.abstract.md", ctx)
        if not overview:
            overview = await read_text_if_exists(viking_fs, f"{dir_uri}/.overview.md", ctx)

        if not abstract and not overview and not scalar_overrides:
            return
        await vectorize_directory_meta(
            dir_uri,
            abstract,
            overview,
            context_type=context_type_for_uri(dir_uri),
            ctx=ctx,
            include_overview=bool(overview),
            scalar_overrides=scalar_overrides,
        )

    async def index_file(file_uri: str, parent_uri: str, name: str, rel_path: str) -> None:
        overrides = _index_scalar_overrides(index_records, rel_path)
        scalar_override = overrides.get(2) or next(iter(overrides.values()), {})
        summary = str(scalar_override.get("abstract") or "")
        await vectorize_file(
            file_path=file_uri,
            summary_dict={"name": name, "summary": summary},
            parent_uri=parent_uri,
            context_type=context_type_for_uri(file_uri),
            ctx=ctx,
            scalar_override=scalar_override,
        )

    await asyncio.gather(*(index_dir(dir_uri) for dir_uri in dir_uris))
    await asyncio.gather(
        *(
            index_file(file_uri, parent_uri, file_name, rel_path)
            for file_uri, parent_uri, file_name, rel_path in file_entries
        )
    )


async def import_ovpack(
    viking_fs,
    file_path: str,
    parent: str,
    ctx: RequestContext,
    on_conflict: Optional[str] = None,
    vector_mode: Optional[str] = None,
    vector_store=None,
) -> str:
    """
    Import .ovpack file to the specified parent path.

    Args:
        viking_fs: VikingFS instance
        file_path: Local .ovpack file path
        parent: Target parent URI (e.g., viking://resources/...)
        on_conflict: One of "fail", "overwrite", or "skip"
        vector_mode: One of "auto", "recompute", or "require"

    Returns:
        Root resource URI after import
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    parent = strip_uri_trailing_slash(parent)
    validate_public_scope(parent, operation="import", allow_root=True)
    conflict_action = normalize_on_conflict(on_conflict)
    vector_action_mode = normalize_vector_mode(vector_mode)
    index_records: list[dict[str, Any]] = []
    dense_vectors: dict[str, list[float]] = {}
    vector_action = "recompute"

    with zipfile.ZipFile(file_path, "r") as zf:
        infolist = zf.infolist()
        if not infolist:
            raise ValueError("Empty ovpack file")

        base_name = base_name_from_entries(infolist)
        manifest = read_manifest(zf, base_name)
        validate_manifest_root_matches_zip(manifest, base_name)
        root_uri = resolve_import_root_uri(parent, base_name, manifest)
        validate_import_scope_compatibility(manifest, root_uri)
        validate_import_target_uri(root_uri)

        members = validated_import_members(infolist, base_name, root_uri)
        existing_roots = [root_uri] if await _root_exists(viking_fs, root_uri, ctx) else []

        if existing_roots:
            if conflict_action == "skip":
                logger.info(f"[ovpack] Skipped existing resource at {root_uri}")
                return root_uri
            if conflict_action == "fail":
                resource = existing_roots[0]
                raise ConflictError(
                    f"Resource already exists at {resource}. "
                    "Use on_conflict='overwrite' to replace it.",
                    resource=resource,
                )

        index_records = validate_manifest_content(zf, manifest, infolist, base_name)
        dense_vectors = read_dense_vectors(zf, manifest, base_name, index_records)
        if VikingURI(root_uri).scope not in NON_VECTOR_SCOPES:
            vector_action = choose_vector_restore_action(
                manifest,
                index_records,
                dense_vectors,
                vector_store=vector_store,
                vector_mode=vector_action_mode,
            )
        if parent != "viking://":
            await _ensure_parent_exists(viking_fs, parent, ctx)

        for existing_root in existing_roots:
            logger.info(f"[ovpack] Overwriting existing resource at {existing_root}")
            await _remove_existing_root(viking_fs, existing_root, ctx)

        for _, safe_zip_path, kind, rel_path in members:
            if kind in {"manifest", "internal"}:
                continue
            if kind == "directory":
                await viking_fs.mkdir(join_uri(root_uri, rel_path), exist_ok=True, ctx=ctx)
                continue

            target_file_uri = join_uri(root_uri, rel_path)
            data = zf.read(safe_zip_path)
            await viking_fs.write_file_bytes(target_file_uri, data, ctx=ctx)

    logger.info(f"[ovpack] Successfully imported {file_path} to {root_uri}")

    if VikingURI(root_uri).scope not in NON_VECTOR_SCOPES:
        if vector_action == "restore":
            await restore_vector_snapshot(vector_store, root_uri, index_records, dense_vectors, ctx)
            logger.info(f"[ovpack] Restored vector snapshot for: {root_uri}")
        else:
            await _enqueue_direct_vectorization(
                viking_fs,
                root_uri,
                ctx=ctx,
                index_records=index_records,
            )
            logger.info(f"[ovpack] Enqueued direct vectorization for: {root_uri}")
    else:
        logger.info(f"[ovpack] Skipped vectorization for non-vector scope: {root_uri}")

    return root_uri


async def _backup_entries(viking_fs, ctx: RequestContext) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for scope in PUBLIC_SCOPES:
        scope_uri = f"viking://{scope}"
        entries.append(
            {
                "rel_path": scope,
                "uri": scope_uri,
                "isDir": True,
                "size": 0,
            }
        )
        try:
            scope_entries = await viking_fs.tree(
                scope_uri,
                show_all_hidden=True,
                node_limit=None,
                level_limit=None,
                ctx=ctx,
            )
        except (NotFoundError, FileNotFoundError):
            continue

        for entry in _exportable_entries(scope_entries):
            rel_path = entry.get("rel_path", "")
            if not rel_path:
                continue
            scoped_entry = dict(entry)
            scoped_entry["rel_path"] = f"{scope}/{rel_path}"
            scoped_entry["uri"] = join_uri(scope_uri, rel_path)
            entries.append(scoped_entry)
    return entries


async def _write_ovpack_archive(
    viking_fs,
    root_uri: str,
    to: str,
    base_name: str,
    entries: list[dict[str, Any]],
    manifest: dict[str, Any],
    index_records: list[dict[str, Any]],
    dense_values: list[float],
    ctx: RequestContext,
) -> str:
    ensure_dir_exists(to)
    manifest_entries = manifest_entries_by_path(manifest)
    manifest_file_entries = {
        rel_path: entry
        for rel_path, entry in manifest_entries.items()
        if entry.get("kind") == "file"
    }

    with zipfile.ZipFile(to, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr(base_name + "/", "")
        zf.writestr(f"{base_name}/{OVPACK_FILES_DIR}/", "")

        content_entries = [entry for entry in entries if entry["rel_path"] != ""]
        for entry in content_entries:
            rel_path = entry["rel_path"]
            zip_path = get_ovpack_zip_path(base_name, rel_path)

            if entry.get("isDir"):
                zf.writestr(zip_path.rstrip("/") + "/", "")
            else:
                full_uri = entry.get("uri") or join_uri(root_uri, rel_path)
                try:
                    data = await viking_fs.read_file_bytes(full_uri, ctx=ctx)
                except Exception as exc:
                    logger.warning(f"Failed to export file {full_uri}: {exc}")
                    raise

                manifest_entry = manifest_file_entries.get(rel_path)
                if manifest_entry is not None:
                    manifest_entry["size"] = len(data)
                    manifest_entry["sha256"] = sha256_hex(data)
                zf.writestr(zip_path, data)

        manifest["content_sha256"] = manifest_content_sha256(manifest_file_entries)
        index_bytes = jsonl_bytes(index_records)
        manifest["index"] = {
            "records": {
                "path": OVPACK_INDEX_RECORDS_PATH,
                "count": len(index_records),
                "sha256": sha256_hex(index_bytes),
            }
        }

        zf.writestr(f"{base_name}/{OVPACK_INTERNAL_DIR}/", "")
        zf.writestr(internal_zip_path(base_name, OVPACK_INDEX_RECORDS_PATH), index_bytes)

        dense_snapshot = build_dense_snapshot_manifest(index_records, dense_values)
        if dense_snapshot is not None:
            dense_bytes, dense_manifest = dense_snapshot
            manifest["index"]["dense"] = dense_manifest
            zf.writestr(internal_zip_path(base_name, dense_manifest["path"]), dense_bytes)

        zf.writestr(
            f"{base_name}/{OVPACK_MANIFEST_ZIP_LEAF}",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        )
    return to


async def export_ovpack(
    viking_fs,
    uri: str,
    to: str,
    ctx: RequestContext,
    vector_store=None,
    include_vectors: bool = False,
) -> str:
    """
    Export the specified context path as a .ovpack file.

    Args:
        viking_fs: VikingFS instance
        uri: Viking URI
        to: Target file path (can be an existing directory or a path ending with .ovpack)
        vector_store: Optional vector store used to export portable index metadata
        include_vectors: Whether to include pure-dense vector snapshots

    Returns:
        Exported file path

    """
    uri = strip_uri_trailing_slash(uri)
    validate_export_source_uri(uri)

    base_name = leaf_name(uri) or "export"

    if os.path.isdir(to):
        to = os.path.join(to, f"{base_name}.ovpack")
    else:
        to = ensure_ovpack_extension(to)

    entries = _exportable_entries(
        await viking_fs.tree(
            uri,
            show_all_hidden=True,
            node_limit=None,
            level_limit=None,
            ctx=ctx,
        )
    )
    if include_vectors:
        ensure_dense_snapshot_supported(vector_store)
        report = await check_index_consistency(
            viking_fs,
            vector_store,
            uri,
            entries,
            ctx,
        )
        if not report.ok:
            raise InvalidArgumentError(
                "Cannot export incomplete OpenViking vector index snapshot",
                details=report.details(),
            )
    manifest, index_records, dense_values = await build_manifest(
        viking_fs,
        vector_store,
        uri,
        base_name,
        entries,
        ctx,
        include_vectors=include_vectors,
    )
    await _write_ovpack_archive(
        viking_fs,
        uri,
        to,
        base_name,
        entries,
        manifest,
        index_records,
        dense_values,
        ctx,
    )

    logger.info(f"[ovpack] Exported {uri} to {to}")
    return to


async def backup_ovpack(
    viking_fs,
    to: str,
    ctx: RequestContext,
    vector_store=None,
    include_vectors: bool = False,
) -> str:
    """Export all public OpenViking scopes as a restore-only backup package."""
    base_name = OVPACK_BACKUP_NAME
    if os.path.isdir(to):
        to = os.path.join(to, f"{base_name}.ovpack")
    else:
        to = ensure_ovpack_extension(to)

    entries = await _backup_entries(viking_fs, ctx)
    if include_vectors:
        ensure_dense_snapshot_supported(vector_store)
        report = await check_index_consistency(
            viking_fs,
            vector_store,
            "viking://",
            entries,
            ctx,
        )
        if not report.ok:
            raise InvalidArgumentError(
                "Cannot export incomplete OpenViking vector index snapshot",
                details=report.details(),
            )
    manifest, index_records, dense_values = await build_manifest(
        viking_fs,
        vector_store,
        "viking://",
        base_name,
        entries,
        ctx,
        package_type=OVPACK_BACKUP_TYPE,
        scopes=list(PUBLIC_SCOPES),
        include_vectors=include_vectors,
    )
    await _write_ovpack_archive(
        viking_fs,
        "viking://",
        to,
        base_name,
        entries,
        manifest,
        index_records,
        dense_values,
        ctx,
    )

    logger.info(f"[ovpack] Backed up OpenViking public scopes to {to}")
    return to


async def restore_ovpack(
    viking_fs,
    file_path: str,
    ctx: RequestContext,
    on_conflict: Optional[str] = None,
    vector_mode: Optional[str] = None,
    vector_store=None,
) -> str:
    """Restore a backup package to its original public scope roots."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    conflict_action = normalize_on_conflict(on_conflict)
    vector_action_mode = normalize_vector_mode(vector_mode)
    root_uri = "viking://"
    index_records: list[dict[str, Any]] = []
    dense_vectors: dict[str, list[float]] = {}
    vector_action = "recompute"

    with zipfile.ZipFile(file_path, "r") as zf:
        infolist = zf.infolist()
        if not infolist:
            raise ValueError("Empty ovpack file")

        base_name = base_name_from_entries(infolist)
        manifest = read_manifest(zf, base_name)
        validate_manifest_root_matches_zip(manifest, base_name)
        if not is_backup_package(manifest):
            raise InvalidArgumentError(
                "Only backup ovpack packages can be restored with ov restore or the restore API",
                details={"root": base_name},
            )

        manifest_entries = manifest_entries_by_path(manifest)
        backup_scopes = backup_scopes_from_manifest(manifest, manifest_entries)
        members = validated_import_members(infolist, base_name, root_uri)
        existing_roots = await _existing_scope_roots(viking_fs, backup_scopes, ctx)

        if existing_roots:
            if conflict_action == "skip":
                logger.info("[ovpack] Skipped backup restore because target scopes exist")
                return root_uri
            if conflict_action == "fail":
                resource = existing_roots[0]
                raise ConflictError(
                    f"Resource already exists at {resource}. "
                    "Use on_conflict='overwrite' to replace it.",
                    resource=resource,
                )

        index_records = validate_manifest_content(zf, manifest, infolist, base_name)
        dense_vectors = read_dense_vectors(zf, manifest, base_name, index_records)
        vector_action = choose_vector_restore_action(
            manifest,
            index_records,
            dense_vectors,
            vector_store=vector_store,
            vector_mode=vector_action_mode,
        )

        for existing_root in existing_roots:
            logger.info(f"[ovpack] Overwriting existing resource at {existing_root}")
            await _remove_existing_root(viking_fs, existing_root, ctx)

        for _, safe_zip_path, kind, rel_path in members:
            if kind in {"manifest", "internal"} or rel_path == "":
                continue
            if kind == "directory":
                await viking_fs.mkdir(join_uri(root_uri, rel_path), exist_ok=True, ctx=ctx)
                continue

            data = zf.read(safe_zip_path)
            await viking_fs.write_file_bytes(join_uri(root_uri, rel_path), data, ctx=ctx)

    logger.info(f"[ovpack] Successfully restored backup {file_path}")

    if vector_action == "restore":
        await restore_vector_snapshot(vector_store, root_uri, index_records, dense_vectors, ctx)
        logger.info("[ovpack] Restored vector snapshot for backup")
        return root_uri

    for scope in backup_scopes:
        if scope in NON_VECTOR_SCOPES:
            logger.info(f"[ovpack] Skipped vectorization for non-vector scope: {scope}")
            continue
        scope_uri = f"viking://{scope}"
        await _enqueue_direct_vectorization(
            viking_fs,
            scope_uri,
            ctx=ctx,
            index_records=index_records,
            manifest_path_root_uri=root_uri,
        )
        logger.info(f"[ovpack] Enqueued direct vectorization for: {scope_uri}")

    return root_uri
