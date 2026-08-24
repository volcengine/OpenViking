# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Content endpoints for OpenViking HTTP Server."""

import asyncio
import os
import tempfile
import zipfile
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import FileResponse
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict, model_validator
from starlette.background import BackgroundTask

from openviking.core.namespace import (
    is_hidden_by_actor_peer_view,
    may_include_hidden_actor_peers,
    resolve_uri,
)
from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.pyagfs.exceptions import AGFSClientError, AGFSNotFoundError
from openviking.resource.processing_mode import DEFAULT_PROCESSING_MODE, ProcessingMode
from openviking.server.auth import (
    get_request_context,
    require_role,
)
from openviking.server.dependencies import get_service
from openviking.server.error_mapping import map_exception
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking.server.telemetry import run_operation
from openviking.telemetry import TelemetryRequest
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    ResourceExhaustedError,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_DIRECTORY_ARCHIVE_MAX_BYTES = 10 * 1024 * 1024


def _archive_size_limit_error(uri: str) -> ResourceExhaustedError:
    return ResourceExhaustedError(
        f"Directory archive exceeds the {_DIRECTORY_ARCHIVE_MAX_BYTES}-byte download limit: {uri}"
    )


def _safe_archive_path(root_name: str, rel_path: str = "") -> str:
    """Build a portable ZIP member path from server-produced tree entries."""
    relative = PurePosixPath(rel_path)
    if relative.is_absolute():
        raise InvalidArgumentError(f"Unsafe path in directory download: {rel_path}")
    parts = [root_name, *relative.parts]
    if any(part in {"", ".", ".."} or "\\" in part for part in parts):
        raise InvalidArgumentError(f"Unsafe path in directory download: {rel_path or root_name}")
    return "/".join(parts)


def _remove_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


async def _build_directory_archive(
    service, uri: str, stat: dict, ctx: RequestContext
) -> tuple[str, str]:
    """Write one visible Viking directory tree to a temporary ZIP archive."""
    root_name = str(stat.get("name") or uri.rstrip("/").rsplit("/", 1)[-1] or "download")
    root_path = _safe_archive_path(root_name)
    fd, archive_path = tempfile.mkstemp(prefix="openviking-download-", suffix=".zip")
    os.close(fd)

    try:
        entries = await service.fs.tree(
            uri,
            ctx=ctx,
            output="original",
            show_all_hidden=True,
            node_limit=None,
            level_limit=None,
        )

        # Reject oversized trees before loading file contents into memory. The
        # ZIP itself is checked below as well because headers can push a highly
        # fragmented archive over the limit even when its files do not.
        declared_total = 0
        for entry in entries:
            if entry.get("isDir", False):
                continue
            size = entry.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                file_stat = await service.fs.stat(str(entry["uri"]), ctx=ctx)
                size = file_stat.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise InvalidArgumentError(
                    f"Cannot determine file size for directory download: {entry['uri']}"
                )
            declared_total += size
            if declared_total > _DIRECTORY_ARCHIVE_MAX_BYTES:
                raise _archive_size_limit_error(uri)

        actual_total = 0
        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive:
            archive.writestr(f"{root_path}/", b"")
            for entry in entries:
                member_path = _safe_archive_path(root_name, str(entry["rel_path"]))
                if entry.get("isDir", False):
                    archive.writestr(f"{member_path.rstrip('/')}/", b"")
                else:
                    content = await service.fs.read_file_bytes(str(entry["uri"]), ctx=ctx)
                    actual_total += len(content)
                    if actual_total > _DIRECTORY_ARCHIVE_MAX_BYTES:
                        raise _archive_size_limit_error(uri)
                    await asyncio.to_thread(archive.writestr, member_path, content)
                # Bound the archive on disk while it grows. Per-entry ZIP headers
                # are not counted by `actual_total`, so a tree of many empty
                # directories or empty files would otherwise only be rejected by
                # the post-build size check, after it was fully written.
                if archive.fp.tell() > _DIRECTORY_ARCHIVE_MAX_BYTES:
                    raise _archive_size_limit_error(uri)
        if os.path.getsize(archive_path) > _DIRECTORY_ARCHIVE_MAX_BYTES:
            raise _archive_size_limit_error(uri)
        return archive_path, f"{root_name}.zip"
    except Exception:
        _remove_file(archive_path)
        raise


class WriteContentRequest(BaseModel):
    """Request to write, append, or create text content to a file."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    content: str
    mode: str = "replace"
    wait: bool = False
    timeout: float | None = None
    telemetry: TelemetryRequest = False
    processing_mode: ProcessingMode = DEFAULT_PROCESSING_MODE


class BatchWriteOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    content: str | None = None
    content_base64: str | None = None
    mode: Literal["replace", "append", "create", "upsert"] = "replace"

    @model_validator(mode="after")
    def validate_content_shape(self) -> "BatchWriteOperation":
        if (self.content is None) == (self.content_base64 is None):
            raise ValueError("exactly one of content or content_base64 is required")
        return self


class BatchWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_uri: str
    operations: list[BatchWriteOperation]
    wait: bool = True
    timeout: float | None = None
    telemetry: TelemetryRequest = False


class SetTagsRequest(BaseModel):
    """Request to set explicit k=v retrieval tags metadata for a file or directory."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    tags: list[str]
    mode: str = "replace"
    recursive: bool = False
    telemetry: TelemetryRequest = False


class ReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    mode: str = "vectors_only"
    wait: bool = True
    dry_run: bool = False
    recursive: bool = True
    tags: list[str] | None = None
    tag_mode: str = "replace"


router = APIRouter(prefix="/api/v1/content", tags=["content"])


def _authorize_reindex_uri(uri: str, ctx: RequestContext) -> str:
    """Allow users to reindex only their own private namespace."""
    if ctx.role != Role.USER:
        return uri

    target = resolve_uri(uri)
    if (
        target.scope != "user"
        or target.owner_user_id != ctx.user.user_id
        or is_hidden_by_actor_peer_view(uri, ctx)
        or may_include_hidden_actor_peers(uri, ctx)
    ):
        raise PermissionDeniedError(
            "USER can only reindex their own user namespace.",
            resource=uri,
        )
    return uri


@router.get("/read")
async def read(
    uri: str = Query(..., description="Viking URI"),
    offset: int = Query(0, description="Starting line number (0-indexed)"),
    limit: int = Query(-1, description="Number of lines to read, -1 means read to end"),
    raw: bool = Query(False, description="Return raw stored content without memory-field cleanup"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Read file content (L2)."""
    service = get_service()
    uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
    try:
        if raw:
            result = await service.fs.read(uri, ctx=_ctx, offset=offset, limit=limit)
        else:
            result = await service.fs.read_visible(uri, ctx=_ctx, offset=offset, limit=limit)
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise

    return Response(status="ok", result=result)


@router.get("/abstract")
async def abstract(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Read L0 abstract."""
    service = get_service()
    uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
    try:
        result = await service.fs.abstract(uri, ctx=_ctx)
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.get("/overview")
async def overview(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Read L1 overview."""
    service = get_service()
    uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
    try:
        result = await service.fs.overview(uri, ctx=_ctx)
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.get("/download")
async def download(
    uri: str = Query(..., description="Viking URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Download a file as raw bytes or a directory as a ZIP archive."""
    service = get_service()
    uri = validate_request_viking_uri(resolve_path_variables(uri), _ctx)
    try:
        stat = await service.fs.stat(uri, ctx=_ctx)
        if stat.get("isDir", False):
            archive_path, filename = await _build_directory_archive(service, uri, stat, _ctx)
            return FileResponse(
                path=archive_path,
                media_type="application/zip",
                filename=filename,
                background=BackgroundTask(_remove_file, archive_path),
            )
        content = await service.fs.read_file_bytes(uri, ctx=_ctx)
    except AGFSNotFoundError:
        raise NotFoundError(uri, "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=uri, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise

    filename = stat.get("name", "download")
    filename = quote(filename)
    return FastAPIResponse(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/write")
async def write(
    request: WriteContentRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Write text content to a file (replace, append, or create) and refresh semantics/vectors."""
    service = get_service()
    uri = validate_request_viking_uri(resolve_path_variables(request.uri), _ctx)
    execution = await run_operation(
        operation="content.write",
        telemetry=request.telemetry,
        fn=lambda: service.fs.write(
            uri=uri,
            content=request.content,
            ctx=_ctx,
            mode=request.mode,
            wait=request.wait,
            timeout=request.timeout,
            processing_mode=request.processing_mode,
        ),
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/batch-write")
async def batch_write(
    request: BatchWriteRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Apply file writes and refresh their indexes once after the batch is written."""
    service = get_service()
    root_uri = validate_request_viking_uri(resolve_path_variables(request.root_uri), _ctx)
    operations = [operation.model_dump(exclude_none=True) for operation in request.operations]
    for operation in operations:
        operation["uri"] = validate_request_viking_uri(
            resolve_path_variables(operation["uri"]), _ctx
        )
    execution = await run_operation(
        operation="content.batch_write",
        telemetry=request.telemetry,
        fn=lambda: service.fs.batch_write(
            root_uri=root_uri,
            operations=operations,
            ctx=_ctx,
            wait=request.wait,
            timeout=request.timeout,
        ),
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/set_tags")
async def set_tags(
    request: SetTagsRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Set explicit k=v retrieval tags metadata for a file or directory."""
    service = get_service()
    uri = validate_request_viking_uri(resolve_path_variables(request.uri), _ctx)
    execution = await run_operation(
        operation="content.set_tags",
        telemetry=request.telemetry,
        fn=lambda: service.fs.set_tags(
            uri=uri,
            tags=request.tags,
            mode=request.mode,
            recursive=request.recursive,
            ctx=_ctx,
        ),
    )
    return Response(
        status="ok",
        result=execution.result,
        telemetry=execution.telemetry,
    ).model_dump(exclude_none=True)


@router.post("/reindex")
async def reindex(
    body: ReindexRequest = Body(...),
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN, Role.USER),
):
    """Reindex semantic/vector artifacts for a URI-scoped maintenance target."""
    if body.dry_run and body.mode != "prune_orphans":
        raise InvalidArgumentError("dry_run is only supported for prune_orphans reindex mode.")
    uri = validate_request_viking_uri(resolve_path_variables(body.uri), ctx)
    uri = _authorize_reindex_uri(uri, ctx)
    service = get_service()
    reindex_kwargs = {
        "uri": uri,
        "mode": body.mode,
        "wait": body.wait,
        "dry_run": body.dry_run,
        "ctx": ctx,
    }
    if not body.recursive:
        reindex_kwargs["recursive"] = False
    if body.tags is not None:
        reindex_kwargs["tags"] = body.tags
        reindex_kwargs["tag_mode"] = body.tag_mode
    result = await service.reindex(
        **reindex_kwargs,
    )
    return Response(status="ok", result=result)
