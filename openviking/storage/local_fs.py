# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import json
import os
import re
import zipfile

from openviking.core.directories import get_context_type_for_uri
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)

_DERIVED_FILENAMES = frozenset({".abstract.md", ".overview.md", ".relations.json"})
_SEMANTIC_REFRESH_IGNORED_FILENAMES = frozenset({".meta.json"})
_MAX_SKIPPED_EXAMPLES = 5

_UNSAFE_PATH_RE = re.compile(r"(^|[\\/])\.\.($|[\\/])")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


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
    """Generate ZIP internal path from relative path, converting components starting with . to _._"""
    parts = rel_path.split("/")
    new_parts = []
    for p in parts:
        if p.startswith("."):
            new_parts.append("_._" + p[1:])
        else:
            new_parts.append(p)
    return f"{base_name}/{'/'.join(new_parts)}"


def get_viking_rel_path_from_zip(zip_path: str) -> str:
    """Restore Viking relative path from ZIP path, converting components starting with _._ back to ."""
    # Remove root directory prefix (base_name/)
    parts = zip_path.split("/")
    if len(parts) <= 1:
        return ""

    # Remove first element (base_name)
    rel_parts = parts[1:]
    new_parts = []
    for p in rel_parts:
        if p.startswith("_._"):
            new_parts.append("." + p[3:])
        else:
            new_parts.append(p)

    return "/".join(new_parts)


def _classify_import_target_uri(
    uri: str, *, allow_soft_skip: bool, is_dir: bool
) -> str:
    """Classify ovpack import target as allow / soft_skip or raise on hard fail."""
    parsed = VikingURI(uri)
    if parsed.scope not in {"resources", "user", "agent"}:
        raise InvalidArgumentError(f"ovpack import is not supported for scope: {parsed.scope}")

    name = uri.rstrip("/").split("/")[-1]
    if is_watch_task_control_uri(uri):
        raise InvalidArgumentError(f"cannot import watch task control file: {uri}")
    if name in _DERIVED_FILENAMES:
        if allow_soft_skip and not is_dir:
            return "soft_skip"
        raise InvalidArgumentError(f"cannot import derived semantic file: {uri}")
    return "allow"


async def _enqueue_semantic_refresh(root_uri: str, ctx: RequestContext) -> None:
    """Enqueue the imported root for normal semantic regeneration."""
    queue_manager = get_queue_manager()
    semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
    msg = SemanticMsg(
        uri=root_uri,
        context_type=get_context_type_for_uri(root_uri),
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        agent_id=ctx.user.agent_id,
        role=ctx.role.value,
        skip_vectorization=False,
    )
    await semantic_queue.enqueue(msg)


def _counts_for_semantic_refresh(uri: str) -> bool:
    """Return True when an imported file should trigger semantic regeneration."""
    name = uri.rstrip("/").split("/")[-1]
    return name not in _SEMANTIC_REFRESH_IGNORED_FILENAMES


def _should_export_ovpack_entry(entry: dict) -> bool:
    """Return True when an entry should be included in ovpack export."""
    if entry.get("isDir"):
        return True
    return entry.get("name") not in _DERIVED_FILENAMES


async def import_ovpack(
    viking_fs,
    file_path: str,
    parent: str,
    ctx: RequestContext,
    force: bool = False,
    vectorize: bool = True,
) -> str:
    """
    Import .ovpack file to the specified parent path.

    Args:
        viking_fs: VikingFS instance
        file_path: Local .ovpack file path
        parent: Target parent URI (e.g., viking://resources/...)
        force: Whether to force overwrite existing resource (default: False)
        vectorize: Deprecated compatibility flag. Import now always refreshes
            semantics via the semantic queue when at least one non-derived file
            is imported.

    Returns:
        Root resource URI after import
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    if vectorize is False:
        logger.debug(
            "[local_fs] import_ovpack received vectorize=False; the flag is kept for "
            "compatibility and no longer changes import behavior"
        )

    parent = parent.strip().rstrip("/")

    with zipfile.ZipFile(file_path, "r") as zf:
        # 1. Get root directory name from ZIP and perform initial validation
        infolist = zf.infolist()
        if not infolist:
            raise ValueError("Empty ovpack file")

        # Extract root directory name (assuming first path component is root name)
        first_path = infolist[0].filename
        # Normalize path separators to handle Windows-created ZIPs
        first_path = first_path.replace("\\", "/")
        base_name = first_path.split("/")[0]
        if not base_name:
            raise ValueError("Could not determine root directory name from ovpack")

        root_uri = f"{parent}/{base_name}"
        _classify_import_target_uri(root_uri, allow_soft_skip=False, is_dir=True)

        try:
            await viking_fs.stat(parent, ctx=ctx)
        except Exception:
            # Parent directory does not exist, create it after target validation.
            await viking_fs.mkdir(parent, ctx=ctx)

        # 2. Conflict check
        try:
            await viking_fs.ls(root_uri, ctx=ctx)
            if not force:
                raise FileExistsError(
                    f"Resource already exists at {root_uri}. Use force=True to overwrite."
                )
            logger.info(f"[local_fs] Overwriting existing resource at {root_uri}")
        except NotFoundError:
            # Path does not exist, safe to import
            pass

        # 3. Validate core metadata _._meta.json (originally .meta.json)
        meta_zip_path = f"{base_name}/_._meta.json"
        try:
            meta_content = zf.read(meta_zip_path)
            meta_data = json.loads(meta_content.decode("utf-8"))
            if "uri" in meta_data and not meta_data["uri"].endswith(base_name):
                logger.warning(
                    f"[local_fs] URI in _._meta.json ({meta_data['uri']}) mismatch with base_name ({base_name})"
                )
        except KeyError:
            logger.warning(
                f"[local_fs] _._meta.json not found in {file_path}, importing without validation"
            )
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON in {meta_zip_path}")

        # 4. Execute import
        semantic_seed_file_count = 0
        skipped_file_count = 0
        skipped_examples: list[str] = []
        for info in infolist:
            zip_path = info.filename
            if not zip_path:
                continue

            # Validate before normalization so backslash paths are rejected
            safe_zip_path = _validate_ovpack_member_path(zip_path, base_name)
            # Normalize path separators to handle Windows-created ZIPs
            safe_zip_path = safe_zip_path.replace("\\", "/")
            is_dir_entry = safe_zip_path.endswith("/")

            # Handle directory entries
            if is_dir_entry:
                rel_path = get_viking_rel_path_from_zip(safe_zip_path.rstrip("/"))
                target_dir_uri = f"{root_uri}/{rel_path}" if rel_path else root_uri
                _classify_import_target_uri(target_dir_uri, allow_soft_skip=False, is_dir=True)
                await viking_fs.mkdir(target_dir_uri, exist_ok=True, ctx=ctx)
                continue

            # Handle file entries
            rel_path = get_viking_rel_path_from_zip(safe_zip_path)
            target_file_uri = f"{root_uri}/{rel_path}" if rel_path else root_uri
            decision = _classify_import_target_uri(
                target_file_uri, allow_soft_skip=True, is_dir=False
            )
            if decision == "soft_skip":
                skipped_file_count += 1
                if len(skipped_examples) < _MAX_SKIPPED_EXAMPLES:
                    skipped_examples.append(target_file_uri)
                continue

            try:
                data = zf.read(safe_zip_path)
                await viking_fs.write_file_bytes(target_file_uri, data, ctx=ctx)
                if _counts_for_semantic_refresh(target_file_uri):
                    semantic_seed_file_count += 1
            except Exception as e:
                logger.error(f"Failed to import {zip_path} to {target_file_uri}: {e}")
                if not force:  # In non-force mode, stop on error
                    raise e

    logger.info(f"[local_fs] Successfully imported {file_path} to {root_uri}")

    if skipped_file_count:
        logger.warning(
            "[local_fs] Skipped %d derived semantic files during ovpack import to %s; "
            "examples=%s. Call wait_processed() if you need regenerated semantics or "
            "retrieval results immediately.",
            skipped_file_count,
            root_uri,
            skipped_examples,
        )

    if semantic_seed_file_count > 0:
        await _enqueue_semantic_refresh(root_uri, ctx=ctx)
        logger.info(f"[local_fs] Enqueued semantic refresh for: {root_uri}")
    else:
        logger.info(
            "[local_fs] Imported no semantic-source files from %s; skipping semantic refresh",
            file_path,
        )

    return root_uri


async def export_ovpack(viking_fs, uri: str, to: str, ctx: RequestContext) -> str:
    """
    Export the specified context path as a .ovpack file.

    Args:
        viking_fs: VikingFS instance
        uri: Viking URI
        to: Target file path (can be an existing directory or a path ending with .ovpack)

    Returns:
        Exported file path

    Raises:
        ValueError: If export size exceeds limits (65536 files or 2GB total size)
    """
    # Safety limits
    MAX_FILES = 65536
    MAX_TOTAL_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

    base_name = uri.strip().rstrip("/").split("/")[-1]
    if not base_name:
        base_name = "export"

    if os.path.isdir(to):
        to = os.path.join(to, f"{base_name}.ovpack")
    else:
        to = ensure_ovpack_extension(to)

    ensure_dir_exists(to)

    entries = await viking_fs.tree(uri, show_all_hidden=True, ctx=ctx)
    entries = [entry for entry in entries if _should_export_ovpack_entry(entry)]

    # Check file count limit
    file_count = sum(1 for entry in entries if not entry.get("isDir"))
    if file_count > MAX_FILES:
        raise ValueError(
            f"Export aborted: too many files ({file_count} files, limit is {MAX_FILES}). "
            f"Please export a smaller directory."
        )

    # Calculate total size and check limit
    total_size = 0
    for entry in entries:
        if not entry.get("isDir"):
            # Get file size from entry if available
            size = entry.get("size", 0)
            total_size += size

    if total_size > MAX_TOTAL_SIZE:
        size_mb = total_size / (1024 * 1024)
        limit_mb = MAX_TOTAL_SIZE / (1024 * 1024)
        raise ValueError(
            f"Export aborted: total size too large ({size_mb:.1f}MB, limit is {limit_mb:.0f}MB). "
            f"Please export a smaller directory."
        )

    with zipfile.ZipFile(to, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        # Write root directory entry
        zf.writestr(base_name + "/", "")

        for entry in entries:
            rel_path = entry["rel_path"]
            zip_path = get_ovpack_zip_path(base_name, rel_path)

            if entry.get("isDir"):
                zf.writestr(zip_path + "/", "")
            else:
                full_uri = f"{uri}/{rel_path}"
                try:
                    data = await viking_fs.read_file_bytes(full_uri, ctx=ctx)
                    zf.writestr(zip_path, data)
                except Exception as e:
                    logger.warning(f"Failed to export file {full_uri}: {e}")

    logger.info(f"[local_fs] Exported {uri} to {to}")
    return to
