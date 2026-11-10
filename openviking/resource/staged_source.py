# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task-owned source snapshots for durable resource ingestion."""

import asyncio
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.server.identity import RequestContext
from openviking.utils.path_safety import safe_join_viking_uri, sanitize_relative_viking_path
from openviking_cli.exceptions import InvalidArgumentError

_COPY_CONCURRENCY = 8
_IDENTITY_META_FIELDS = frozenset(
    {
        "extension",
        "resolved_extension",
        "resolved_name",
        "original_filename",
    }
)


@dataclass(frozen=True)
class StagedSource:
    """A LocalResource copied into task-owned VikingFS temporary storage."""

    temp_uri: str
    source_uri: str
    source_type: str
    original_source: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StagedSource":
        if not isinstance(data, dict):
            raise ValueError("staged_source must be an object")
        temp_uri = data.get("temp_uri")
        source_uri = data.get("source_uri")
        source_type = data.get("source_type")
        original_source = data.get("original_source")
        meta = data.get("meta")
        if not isinstance(temp_uri, str) or not temp_uri.startswith("viking://temp/"):
            raise ValueError("staged_source.temp_uri must be a temp URI")
        bundle_uri = f"{temp_uri.rstrip('/')}/source/"
        if not isinstance(source_uri, str) or not source_uri.startswith(bundle_uri):
            raise ValueError("staged_source.source_uri must be inside its temp bundle")
        if source_type not in {
            SourceType.LOCAL,
            SourceType.GIT,
            SourceType.HTTP,
            SourceType.FEISHU,
        }:
            raise ValueError("staged_source.source_type is invalid")
        if not isinstance(original_source, str):
            raise ValueError("staged_source.original_source must be a string")
        if not isinstance(meta, dict):
            raise ValueError("staged_source.meta must be an object")
        return cls(
            temp_uri=temp_uri.rstrip("/"),
            source_uri=source_uri.rstrip("/"),
            source_type=source_type,
            original_source=original_source,
            meta=dict(meta),
        )


async def stage_source(
    resource: LocalResource,
    *,
    viking_fs: Any,
    ctx: RequestContext,
) -> StagedSource:
    """Copy one prepared source into task-owned VikingFS storage."""
    path = resource.path
    if not path.is_file() and not path.is_dir():
        raise InvalidArgumentError("Resource source is not a regular file or directory.")

    temp_uri = viking_fs.create_temp_uri(ctx=ctx).rstrip("/")
    source_uri = safe_join_viking_uri(f"{temp_uri}/source", path.name or "resource")
    try:
        if path.is_dir():
            await _copy_local_tree(path, source_uri, viking_fs, ctx)
        else:
            await viking_fs.write_file_bytes(
                source_uri,
                await asyncio.to_thread(path.read_bytes),
                ctx=ctx,
            )
    except BaseException:
        await viking_fs.delete_temp(temp_uri, ctx=ctx)
        raise

    return StagedSource(
        temp_uri=temp_uri,
        source_uri=source_uri,
        source_type=resource.source_type,
        original_source=resource.original_source,
        meta={key: resource.meta[key] for key in _IDENTITY_META_FIELDS if key in resource.meta},
    )


async def materialize_source(
    staged: StagedSource,
    *,
    viking_fs: Any,
    ctx: RequestContext,
) -> LocalResource:
    """Restore a staged source into worker-local temporary storage."""
    local_root = Path(tempfile.mkdtemp(prefix="ov_staged_source_"))
    bundle_uri = f"{staged.temp_uri}/source"
    source_relpath = staged.source_uri.removeprefix(f"{bundle_uri}/")
    try:
        entries = await viking_fs.tree(
            bundle_uri,
            output="original",
            show_all_hidden=True,
            node_limit=None,
            level_limit=None,
            ctx=ctx,
        )
        for entry in entries:
            target = _local_target(local_root, entry.get("rel_path"))
            if entry.get("isDir", False):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            content = await viking_fs.read_file_bytes(str(entry["uri"]), ctx=ctx)
            await asyncio.to_thread(target.write_bytes, content)

        local_path = _local_target(local_root, source_relpath)
        if not local_path.exists():
            raise ValueError("Staged source is missing")
    except BaseException:
        shutil.rmtree(local_root, ignore_errors=True)
        raise

    meta = dict(staged.meta)
    meta["_cleanup_path"] = str(local_root)
    return LocalResource(
        path=local_path,
        source_type=staged.source_type,
        original_source=staged.original_source,
        meta=meta,
        is_temporary=True,
    )


async def _copy_local_tree(
    local_dir: Path,
    target_uri: str,
    viking_fs: Any,
    ctx: RequestContext,
) -> None:
    directories = {target_uri}
    files: list[tuple[Path, str]] = []
    for root, dir_names, file_names in os.walk(local_dir, followlinks=False):
        root_path = Path(root)
        dir_names[:] = [name for name in dir_names if not (root_path / name).is_symlink()]
        relative_root = root_path.relative_to(local_dir)
        if relative_root.parts:
            directories.add(safe_join_viking_uri(target_uri, relative_root.as_posix()))
        for name in file_names:
            local_path = root_path / name
            if local_path.is_symlink() or not local_path.is_file():
                continue
            target_file_uri = safe_join_viking_uri(
                target_uri,
                local_path.relative_to(local_dir).as_posix(),
            )
            directories.add(target_file_uri.rsplit("/", 1)[0])
            files.append((local_path, target_file_uri))

    for directory_uri in sorted(directories):
        await viking_fs.mkdir(directory_uri, exist_ok=True, ctx=ctx)

    semaphore = asyncio.Semaphore(_COPY_CONCURRENCY)

    async def copy_file(local_path: Path, target_file_uri: str) -> None:
        async with semaphore:
            content = await asyncio.to_thread(local_path.read_bytes)
            await viking_fs.write_file_bytes(target_file_uri, content, ctx=ctx)

    await asyncio.gather(*(copy_file(path, uri) for path, uri in files))


def _local_target(root: Path, rel_path: Any) -> Path:
    if not isinstance(rel_path, str):
        raise ValueError("Staged source entry is missing rel_path")
    normalized = sanitize_relative_viking_path(rel_path)
    return root.joinpath(*Path(normalized).parts)
