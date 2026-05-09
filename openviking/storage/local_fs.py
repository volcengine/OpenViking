# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import asyncio
import hashlib
import json
import os
import re
import zipfile
from typing import Any, Optional

from openviking.core.namespace import context_type_for_uri
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext
from openviking.storage.expr import Eq
from openviking.utils.embedding_utils import vectorize_directory_meta, vectorize_file
from openviking_cli.exceptions import ConflictError, InvalidArgumentError, NotFoundError
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)

OVPACK_FORMAT_VERSION = 2
OVPACK_KIND = "openviking.ovpack"
OVPACK_MANIFEST_FILENAME = ".ovpack_manifest.json"
OVPACK_MANIFEST_ZIP_LEAF = "_._ovpack_manifest.json"
OVPACK_ON_CONFLICT_VALUES = frozenset({"fail", "overwrite", "skip"})

_IMPORTABLE_SCOPES = frozenset({"resources", "user", "agent"})
_DERIVED_FILENAMES = frozenset(
    {".abstract.md", ".overview.md", ".relations.json", OVPACK_MANIFEST_FILENAME}
)
_PORTABLE_VECTOR_SCALAR_FIELDS = [
    "uri",
    "type",
    "context_type",
    "level",
    "name",
    "description",
    "tags",
    "abstract",
]

_UNSAFE_PATH_RE = re.compile(r"(^|[\\/])\.\.($|[\\/])")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _strip_uri_trailing_slash(uri: str) -> str:
    normalized = VikingURI(uri.strip()).uri
    return normalized if normalized == "viking://" else normalized.rstrip("/")


def _join_uri(base_uri: str, rel_path: str) -> str:
    base_uri = _strip_uri_trailing_slash(base_uri)
    return f"{base_uri}/{rel_path}" if rel_path else base_uri


def _rel_path_for_uri(root_uri: str, uri: str) -> str:
    root_uri = _strip_uri_trailing_slash(root_uri)
    uri = _strip_uri_trailing_slash(uri)
    if uri == root_uri:
        return ""
    prefix = f"{root_uri}/"
    return uri[len(prefix) :] if uri.startswith(prefix) else ""


def _leaf_name(uri_or_path: str) -> str:
    return uri_or_path.rstrip("/").split("/")[-1]


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_derived_rel_path(rel_path: str) -> bool:
    return _leaf_name(rel_path) in _DERIVED_FILENAMES


def _is_manifest_zip_path(zip_path: str, base_name: str) -> bool:
    return zip_path == f"{base_name}/{OVPACK_MANIFEST_ZIP_LEAF}"


def _validate_ovpack_member_path(zip_path: str, base_name: str) -> str:
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
    if not rel_path:
        return f"{base_name}/"
    parts = rel_path.split("/")
    escaped = [("_._" + part[1:]) if part.startswith(".") else part for part in parts]
    return f"{base_name}/{'/'.join(escaped)}"


def get_viking_rel_path_from_zip(zip_path: str) -> str:
    """Restore Viking relative path from ZIP path."""
    parts = zip_path.split("/")
    if len(parts) <= 1:
        return ""

    rel_parts = parts[1:]
    restored = [("." + part[3:]) if part.startswith("_._") else part for part in rel_parts]
    return "/".join(restored)


def _validate_scope(uri: str, *, operation: str) -> None:
    parsed = VikingURI(uri)
    if parsed.scope not in _IMPORTABLE_SCOPES:
        raise InvalidArgumentError(f"ovpack {operation} is not supported for scope: {parsed.scope}")
    if parsed.uri == "viking://":
        raise InvalidArgumentError(f"ovpack {operation} is not supported for root URI")


def _validate_import_target_uri(uri: str) -> None:
    """Enforce the same target-policy boundary as direct content writes."""
    _validate_scope(uri, operation="import")
    name = _leaf_name(uri)
    if name in _DERIVED_FILENAMES:
        raise InvalidArgumentError(f"cannot import derived semantic file: {uri}")
    if is_watch_task_control_uri(uri):
        raise InvalidArgumentError(f"cannot import watch task control file: {uri}")


def _validate_export_source_uri(uri: str) -> None:
    _validate_scope(uri, operation="export")
    name = _leaf_name(uri)
    if name in _DERIVED_FILENAMES:
        raise InvalidArgumentError(f"cannot export derived semantic file: {uri}")
    if is_watch_task_control_uri(uri):
        raise InvalidArgumentError(f"cannot export watch task control file: {uri}")


def _base_name_from_entries(infolist: list[zipfile.ZipInfo]) -> str:
    for info in infolist:
        filename = info.filename
        if filename:
            base_name = filename.replace("\\", "/").split("/")[0]
            if base_name:
                return base_name
    raise ValueError("Could not determine root directory name from ovpack")


def _normalize_on_conflict(on_conflict: Optional[str]) -> str:
    if on_conflict is None:
        return "fail"
    if on_conflict not in OVPACK_ON_CONFLICT_VALUES:
        allowed = ", ".join(sorted(OVPACK_ON_CONFLICT_VALUES))
        raise InvalidArgumentError(
            f"Invalid on_conflict value: {on_conflict}. Must be one of: {allowed}"
        )
    return on_conflict


def _portable_scalars(record: dict[str, Any]) -> dict[str, Any]:
    return {
        field: record[field]
        for field in _PORTABLE_VECTOR_SCALAR_FIELDS
        if field != "uri" and record.get(field) is not None
    }


def _record_level(record: dict[str, Any], default: int = 2) -> int:
    try:
        return int(record.get("level", default))
    except (TypeError, ValueError):
        return default


async def _call_vector_filter(vector_store, uri: str, ctx: RequestContext) -> list[dict[str, Any]]:
    if not vector_store or not hasattr(vector_store, "filter"):
        return []

    kwargs = {
        "filter": Eq("uri", uri),
        "limit": 10,
        "output_fields": _PORTABLE_VECTOR_SCALAR_FIELDS,
    }
    try:
        return await vector_store.filter(**kwargs, ctx=ctx)
    except TypeError:
        try:
            return await vector_store.filter(**kwargs)
        except Exception as exc:
            logger.warning(f"Failed to export vector scalars for {uri}: {exc}")
    except Exception as exc:
        logger.warning(f"Failed to export vector scalars for {uri}: {exc}")
    return []


async def _read_text_if_exists(viking_fs, uri: str, ctx: RequestContext) -> str:
    try:
        if not await viking_fs.exists(uri, ctx=ctx):
            return ""
        content = await viking_fs.read_file(uri, ctx=ctx)
        return content.decode("utf-8") if isinstance(content, bytes) else content
    except Exception:
        return ""


async def _directory_vector_texts(viking_fs, uri: str, ctx: RequestContext) -> dict[int, str]:
    abstract = await _read_text_if_exists(viking_fs, f"{uri}/.abstract.md", ctx)
    overview = await _read_text_if_exists(viking_fs, f"{uri}/.overview.md", ctx)
    return {0: abstract, 1: overview}


async def _manifest_vector_records(
    viking_fs,
    vector_store,
    uri: str,
    is_dir: bool,
    ctx: RequestContext,
) -> list[dict[str, Any]]:
    records = await _call_vector_filter(vector_store, uri, ctx)
    records_by_level = {_record_level(record): record for record in records}
    texts = await _directory_vector_texts(viking_fs, uri, ctx) if is_dir else {}

    manifest_records: list[dict[str, Any]] = []
    for level in sorted(records_by_level):
        item = {
            "level": level,
            "scalars": _portable_scalars(records_by_level[level]),
        }
        text = texts.get(level)
        if text:
            item["text"] = text
        manifest_records.append(item)

    if is_dir:
        abstract = texts.get(0, "")
        for level, text in texts.items():
            if text and level not in records_by_level:
                manifest_records.append(
                    {
                        "level": level,
                        "text": text,
                        "scalars": {
                            "context_type": context_type_for_uri(uri),
                            "level": level,
                            "abstract": abstract,
                        },
                    }
                )

    return sorted(manifest_records, key=lambda item: int(item.get("level", 2)))


async def _build_manifest(
    viking_fs,
    vector_store,
    root_uri: str,
    base_name: str,
    entries: list[dict[str, Any]],
    ctx: RequestContext,
) -> dict[str, Any]:
    manifest_entries = [{"path": "", "kind": "directory"}]
    vectors: dict[str, list[dict[str, Any]]] = {}

    root_vectors = await _manifest_vector_records(
        viking_fs, vector_store, root_uri, is_dir=True, ctx=ctx
    )
    if root_vectors:
        vectors[""] = root_vectors

    for entry in entries:
        rel_path = entry["rel_path"]
        is_dir = bool(entry.get("isDir"))
        manifest_entries.append(
            {
                "path": rel_path,
                "kind": "directory" if is_dir else "file",
                "size": entry.get("size", 0) if not is_dir else 0,
            }
        )
        records = await _manifest_vector_records(
            viking_fs,
            vector_store,
            _join_uri(root_uri, rel_path),
            is_dir=is_dir,
            ctx=ctx,
        )
        if records:
            vectors[rel_path] = records

    return {
        "kind": OVPACK_KIND,
        "format_version": OVPACK_FORMAT_VERSION,
        "root": {
            "name": base_name,
            "uri": root_uri,
            "scope": VikingURI(root_uri).scope,
        },
        "entries": manifest_entries,
        "vectors": vectors,
    }


def _invalid_manifest(message: str, manifest_path: str, **details: Any) -> InvalidArgumentError:
    return InvalidArgumentError(
        message,
        details={"manifest_path": manifest_path, **details},
    )


def _read_manifest(zf: zipfile.ZipFile, base_name: str) -> dict[str, Any]:
    manifest_path = f"{base_name}/{OVPACK_MANIFEST_ZIP_LEAF}"
    try:
        raw = zf.read(manifest_path)
    except KeyError:
        raise _invalid_manifest(
            "Missing ovpack manifest",
            manifest_path,
            hint="Re-export this package with OVPack v2 checksum support before importing.",
        )

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise _invalid_manifest(
            "Invalid JSON in ovpack manifest",
            manifest_path,
            reason=str(exc),
        ) from exc
    if not isinstance(manifest, dict):
        raise _invalid_manifest(
            "Invalid ovpack manifest",
            manifest_path,
            actual_type=type(manifest).__name__,
        )

    version = manifest.get("format_version")
    if version is None:
        raise _invalid_manifest(
            "Missing ovpack format_version",
            manifest_path,
            field="format_version",
        )
    try:
        version_int = int(version)
    except (TypeError, ValueError) as exc:
        raise _invalid_manifest(
            f"Invalid ovpack format_version {version!r}",
            manifest_path,
            field="format_version",
            value=version,
        ) from exc
    if version_int < 1:
        raise _invalid_manifest(
            f"Invalid ovpack format_version {version!r}",
            manifest_path,
            field="format_version",
            value=version,
        )
    if version_int != OVPACK_FORMAT_VERSION:
        raise _invalid_manifest(
            f"Unsupported ovpack format_version {version}; "
            f"this OpenViking requires {OVPACK_FORMAT_VERSION}",
            manifest_path,
            format_version=version_int,
            supported_format_version=OVPACK_FORMAT_VERSION,
        )
    if manifest.get("kind") != OVPACK_KIND:
        raise _invalid_manifest(
            "Invalid ovpack manifest kind",
            manifest_path,
            expected=OVPACK_KIND,
            actual=manifest.get("kind"),
        )
    return manifest


def _manifest_records_by_level(
    manifest: dict[str, Any], rel_path: str
) -> dict[int, dict[str, Any]]:
    vectors = manifest.get("vectors") if isinstance(manifest, dict) else None
    records = vectors.get(rel_path, []) if isinstance(vectors, dict) else []
    if not isinstance(records, list):
        return {}

    by_level: dict[int, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            level = int(record.get("level", 2))
        except (TypeError, ValueError):
            continue
        by_level[level] = record
    return by_level


def _manifest_scalar_overrides(
    manifest: dict[str, Any], rel_path: str
) -> dict[int, dict[str, Any]]:
    overrides: dict[int, dict[str, Any]] = {}
    for level, record in _manifest_records_by_level(manifest, rel_path).items():
        scalars = record.get("scalars")
        if isinstance(scalars, dict):
            overrides[level] = dict(scalars)
    return overrides


def _normalize_sha256(value: Any, *, field: str, path: str | None = None) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        details = {"field": field}
        if path is not None:
            details["path"] = path
        raise InvalidArgumentError(f"Invalid ovpack manifest {field}", details=details)
    return value.lower()


def _manifest_entries_by_path(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
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
        by_path[rel_path] = entry

    return by_path


def _manifest_content_sha256(file_entries_by_path: dict[str, dict[str, Any]]) -> str:
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
                "sha256": _normalize_sha256(entry.get("sha256"), field="sha256", path=rel_path),
            }
        )

    payload = json.dumps(
        content_entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_hex(payload)


def _zip_file_members_by_path(
    infolist: list[zipfile.ZipInfo], base_name: str
) -> dict[str, tuple[zipfile.ZipInfo, str]]:
    files: dict[str, tuple[zipfile.ZipInfo, str]] = {}
    for info in infolist:
        zip_path = info.filename
        if not zip_path:
            continue
        safe_zip_path = _validate_ovpack_member_path(zip_path, base_name)
        if _is_manifest_zip_path(safe_zip_path, base_name) or safe_zip_path.endswith("/"):
            continue

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
        safe_zip_path = _validate_ovpack_member_path(zip_path, base_name)
        if not safe_zip_path.endswith("/"):
            continue

        rel_path = get_viking_rel_path_from_zip(safe_zip_path.rstrip("/"))
        if rel_path in directories:
            raise InvalidArgumentError(
                "Duplicate ovpack directory entry",
                details={"path": rel_path},
            )
        directories.add(rel_path)
    return directories


def _validate_manifest_content(
    zf: zipfile.ZipFile,
    manifest: dict[str, Any],
    infolist: list[zipfile.ZipInfo],
    base_name: str,
) -> None:
    if "entries" not in manifest:
        raise InvalidArgumentError(
            "Missing ovpack manifest entries",
            details={"field": "entries"},
        )

    manifest_entries = _manifest_entries_by_path(manifest)
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
    expected_content_sha256 = _normalize_sha256(
        expected_content_sha256,
        field="content_sha256",
    )
    actual_content_sha256 = _manifest_content_sha256(manifest_files)
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
            expected_sha256 = _normalize_sha256(
                expected_sha256,
                field="sha256",
                path=rel_path,
            )
            actual_sha256 = _sha256_hex(data)
            if actual_sha256 != expected_sha256:
                raise InvalidArgumentError(
                    "ovpack file sha256 does not match manifest",
                    details={
                        "path": rel_path,
                        "expected": expected_sha256,
                        "actual": actual_sha256,
                    },
                )


def _validated_import_members(
    infolist: list[zipfile.ZipInfo], base_name: str, root_uri: str
) -> list[tuple[zipfile.ZipInfo, str, str, str]]:
    members: list[tuple[zipfile.ZipInfo, str, str, str]] = []
    for info in infolist:
        zip_path = info.filename
        if not zip_path:
            continue

        safe_zip_path = _validate_ovpack_member_path(zip_path, base_name)
        if _is_manifest_zip_path(safe_zip_path, base_name):
            members.append((info, safe_zip_path, "manifest", ""))
            continue

        kind = "directory" if safe_zip_path.endswith("/") else "file"
        rel_path = get_viking_rel_path_from_zip(
            safe_zip_path.rstrip("/") if kind == "directory" else safe_zip_path
        )
        if _is_derived_rel_path(rel_path):
            logger.info(f"[local_fs] Skipping derived ovpack entry: {safe_zip_path}")
            continue
        target_uri = _join_uri(root_uri, rel_path)
        _validate_import_target_uri(target_uri)
        members.append((info, safe_zip_path, kind, rel_path))

    return members


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
        logger.warning(f"[local_fs] Cannot remove existing resource without rm(): {root_uri}")
        return
    try:
        await viking_fs.rm(root_uri, recursive=True, ctx=ctx)
    except NotFoundError:
        return
    except FileNotFoundError:
        return


def _exportable_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if not _is_derived_rel_path(entry.get("rel_path", ""))]


async def _enqueue_direct_vectorization(
    viking_fs,
    uri: str,
    ctx: RequestContext,
    manifest: Optional[dict[str, Any]] = None,
) -> None:
    manifest = manifest or {}
    entries = await viking_fs.tree(uri, node_limit=100000, level_limit=1000, ctx=ctx)
    dir_uris = {uri}
    file_entries: list[tuple[str, str, str, str]] = []
    for entry in entries:
        entry_uri = entry.get("uri")
        if not entry_uri:
            continue
        rel_path = entry.get("rel_path") or _rel_path_for_uri(uri, entry_uri)
        if entry.get("isDir"):
            dir_uris.add(entry_uri)
            continue
        name = entry.get("name", "") or _leaf_name(rel_path)
        if name.startswith("."):
            continue
        parent = VikingURI(entry_uri).parent
        if parent:
            file_entries.append((entry_uri, parent.uri, name, rel_path))

    async def index_dir(dir_uri: str) -> None:
        rel_path = _rel_path_for_uri(uri, dir_uri)
        records_by_level = _manifest_records_by_level(manifest, rel_path)
        scalar_overrides = _manifest_scalar_overrides(manifest, rel_path)
        abstract = str(records_by_level.get(0, {}).get("text") or "")
        overview = str(records_by_level.get(1, {}).get("text") or "")

        if not abstract:
            abstract = await _read_text_if_exists(viking_fs, f"{dir_uri}/.abstract.md", ctx)
        if not overview:
            overview = await _read_text_if_exists(viking_fs, f"{dir_uri}/.overview.md", ctx)

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
        overrides = _manifest_scalar_overrides(manifest, rel_path)
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
) -> str:
    """
    Import .ovpack file to the specified parent path.

    Args:
        viking_fs: VikingFS instance
        file_path: Local .ovpack file path
        parent: Target parent URI (e.g., viking://resources/...)
        on_conflict: One of "fail", "overwrite", or "skip"

    Returns:
        Root resource URI after import
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    parent = _strip_uri_trailing_slash(parent)
    _validate_scope(parent, operation="import")
    conflict_action = _normalize_on_conflict(on_conflict)

    with zipfile.ZipFile(file_path, "r") as zf:
        infolist = zf.infolist()
        if not infolist:
            raise ValueError("Empty ovpack file")

        base_name = _base_name_from_entries(infolist)
        root_uri = _join_uri(parent, base_name)
        _validate_import_target_uri(root_uri)
        manifest = _read_manifest(zf, base_name)
        manifest_root = manifest.get("root") if isinstance(manifest.get("root"), dict) else {}
        if manifest_root.get("name") and manifest_root.get("name") != base_name:
            logger.warning(
                f"[local_fs] Manifest root name ({manifest_root.get('name')}) "
                f"does not match zip root ({base_name})"
            )

        members = _validated_import_members(infolist, base_name, root_uri)
        root_exists = await _root_exists(viking_fs, root_uri, ctx)

        if root_exists:
            if conflict_action == "skip":
                logger.info(f"[local_fs] Skipped existing resource at {root_uri}")
                return root_uri
            if conflict_action == "fail":
                raise ConflictError(
                    f"Resource already exists at {root_uri}. "
                    "Use on_conflict='overwrite' to replace it.",
                    resource=root_uri,
                )

        _validate_manifest_content(zf, manifest, infolist, base_name)
        await _ensure_parent_exists(viking_fs, parent, ctx)

        if root_exists:
            logger.info(f"[local_fs] Overwriting existing resource at {root_uri}")
            await _remove_existing_root(viking_fs, root_uri, ctx)

        for _, safe_zip_path, kind, rel_path in members:
            if kind == "manifest":
                continue
            if kind == "directory":
                await viking_fs.mkdir(_join_uri(root_uri, rel_path), exist_ok=True, ctx=ctx)
                continue

            target_file_uri = _join_uri(root_uri, rel_path)
            data = zf.read(safe_zip_path)
            await viking_fs.write_file_bytes(target_file_uri, data, ctx=ctx)

    logger.info(f"[local_fs] Successfully imported {file_path} to {root_uri}")

    await _enqueue_direct_vectorization(viking_fs, root_uri, ctx=ctx, manifest=manifest)
    logger.info(f"[local_fs] Enqueued direct vectorization for: {root_uri}")

    return root_uri


async def export_ovpack(
    viking_fs,
    uri: str,
    to: str,
    ctx: RequestContext,
    vector_store=None,
) -> str:
    """
    Export the specified context path as a .ovpack file.

    Args:
        viking_fs: VikingFS instance
        uri: Viking URI
        to: Target file path (can be an existing directory or a path ending with .ovpack)
        vector_store: Optional vector store used to export portable scalar metadata

    Returns:
        Exported file path

    Raises:
        ValueError: If export size exceeds limits (65536 files or 2GB total size)
    """
    MAX_FILES = 65536
    MAX_TOTAL_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

    uri = _strip_uri_trailing_slash(uri)
    _validate_export_source_uri(uri)

    base_name = _leaf_name(uri) or "export"

    if os.path.isdir(to):
        to = os.path.join(to, f"{base_name}.ovpack")
    else:
        to = ensure_ovpack_extension(to)

    ensure_dir_exists(to)

    entries = _exportable_entries(await viking_fs.tree(uri, show_all_hidden=True, ctx=ctx))

    file_count = sum(1 for entry in entries if not entry.get("isDir"))
    if file_count > MAX_FILES:
        raise ValueError(
            f"Export aborted: too many files ({file_count} files, limit is {MAX_FILES}). "
            f"Please export a smaller directory."
        )

    total_size = sum(entry.get("size", 0) for entry in entries if not entry.get("isDir"))
    if total_size > MAX_TOTAL_SIZE:
        size_mb = total_size / (1024 * 1024)
        limit_mb = MAX_TOTAL_SIZE / (1024 * 1024)
        raise ValueError(
            f"Export aborted: total size too large ({size_mb:.1f}MB, limit is {limit_mb:.0f}MB). "
            f"Please export a smaller directory."
        )

    manifest = await _build_manifest(viking_fs, vector_store, uri, base_name, entries, ctx)
    manifest_entries = _manifest_entries_by_path(manifest)
    manifest_file_entries = {
        rel_path: entry
        for rel_path, entry in manifest_entries.items()
        if entry.get("kind") == "file"
    }

    with zipfile.ZipFile(to, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr(base_name + "/", "")

        for entry in entries:
            rel_path = entry["rel_path"]
            zip_path = get_ovpack_zip_path(base_name, rel_path)

            if entry.get("isDir"):
                zf.writestr(zip_path + "/", "")
            else:
                full_uri = _join_uri(uri, rel_path)
                try:
                    data = await viking_fs.read_file_bytes(full_uri, ctx=ctx)
                except Exception as exc:
                    logger.warning(f"Failed to export file {full_uri}: {exc}")
                    raise

                manifest_entry = manifest_file_entries.get(rel_path)
                if manifest_entry is not None:
                    manifest_entry["size"] = len(data)
                    manifest_entry["sha256"] = _sha256_hex(data)
                zf.writestr(zip_path, data)

        manifest["content_sha256"] = _manifest_content_sha256(manifest_file_entries)
        zf.writestr(
            f"{base_name}/{OVPACK_MANIFEST_ZIP_LEAF}",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        )

    logger.info(f"[local_fs] Exported {uri} to {to}")
    return to
