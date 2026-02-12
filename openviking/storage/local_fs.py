# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
import os
import zipfile
from datetime import datetime
from typing import cast

from openviking.core.context import Context
from openviking.storage.queuefs import EmbeddingQueue, get_queue_manager
from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter
from openviking_cli.utils.logger import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)


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


# TODO: Consider recursive vectorization
async def _enqueue_direct_vectorization(viking_fs, uri: str) -> None:
    queue_manager = get_queue_manager()
    embedding_queue = cast(
        EmbeddingQueue, queue_manager.get_queue(queue_manager.EMBEDDING, allow_create=True)
    )

    parent_uri = VikingURI(uri).parent.uri
    abstract = await viking_fs.abstract(uri)
    resource = Context(
        uri=uri,
        parent_uri=parent_uri,
        is_leaf=False,
        abstract=abstract,
        created_at=datetime.now(),
        active_count=0,
        related_uri=[],
        meta={"semantic_name": uri.split("/")[-1]},
    )

    embedding_msg = EmbeddingMsgConverter.from_context(resource)
    await embedding_queue.enqueue(embedding_msg)


async def import_ovpack(
    viking_fs, file_path: str, parent: str, force: bool = False, vectorize: bool = True
) -> str:
    """
    Import .ovpack file to the specified parent path.

    Args:
        viking_fs: VikingFS instance
        file_path: Local .ovpack file path
        parent: Target parent URI (e.g., viking://resources/...)
        force: Whether to force overwrite existing resource (default: False)
        vectorize: Whether to trigger vectorization (default: True)

    Returns:
        Root resource URI after import
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    parent = parent.strip().rstrip("/")

    try:
        await viking_fs.stat(parent)
    except Exception:
        # Parent directory does not exist, create it
        await viking_fs.mkdir(parent)

    with zipfile.ZipFile(file_path, "r") as zf:
        # 1. Get root directory name from ZIP and perform initial validation
        infolist = zf.infolist()
        if not infolist:
            raise ValueError("Empty ovpack file")

        # Extract root directory name (assuming first path component is root name)
        first_path = infolist[0].filename
        base_name = first_path.split("/")[0]
        if not base_name:
            raise ValueError("Could not determine root directory name from ovpack")

        root_uri = f"{parent}/{base_name}"

        # 2. Conflict check
        try:
            await viking_fs.ls(root_uri)
            if not force:
                raise FileExistsError(
                    f"Resource already exists at {root_uri}. Use force=True to overwrite."
                )
            logger.info(f"[local_fs] Overwriting existing resource at {root_uri}")
        except FileNotFoundError:
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
        for info in infolist:
            zip_path = info.filename
            if not zip_path:
                continue

            # Handle directory entries
            if zip_path.endswith("/"):
                rel_path = get_viking_rel_path_from_zip(zip_path.rstrip("/"))
                target_dir_uri = f"{root_uri}/{rel_path}" if rel_path else root_uri
                await viking_fs.mkdir(target_dir_uri, exist_ok=True)
                continue

            # Handle file entries
            rel_path = get_viking_rel_path_from_zip(zip_path)
            target_file_uri = f"{root_uri}/{rel_path}" if rel_path else root_uri

            try:
                data = zf.read(zip_path)
                await viking_fs.write_file_bytes(target_file_uri, data)
            except Exception as e:
                logger.error(f"Failed to import {zip_path} to {target_file_uri}: {e}")
                if not force:  # In non-force mode, stop on error
                    raise e

    logger.info(f"[local_fs] Successfully imported {file_path} to {root_uri}")

    if vectorize:
        await _enqueue_direct_vectorization(viking_fs, root_uri)
        logger.info(f"[local_fs] Enqueued direct vectorization for: {root_uri}")

    return root_uri


async def export_ovpack(viking_fs, uri: str, to: str) -> str:
    """
    Export the specified context path as a .ovpack file.

    Args:
        viking_fs: VikingFS instance
        uri: Viking URI
        to: Target file path (can be an existing directory or a path ending with .ovpack)

    Returns:
        Exported file path
    """
    base_name = uri.strip().rstrip("/").split("/")[-1]
    if not base_name:
        base_name = "export"

    if os.path.isdir(to):
        to = os.path.join(to, f"{base_name}.ovpack")
    else:
        to = ensure_ovpack_extension(to)

    ensure_dir_exists(to)

    entries = await viking_fs.tree(uri)

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
                    data = await viking_fs.read_file_bytes(full_uri)
                    zf.writestr(zip_path, data)
                except Exception as e:
                    logger.warning(f"Failed to export file {full_uri}: {e}")

    logger.info(f"[local_fs] Exported {uri} to {to}")
    return to
