# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP routes for git-style version control (snapshots).

Mirrors VikingFS.commit / VikingFS.restore / VikingFS.show / VikingFS.diff / VikingFS.log,
which already implement the underlying semantics.
"""

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict

from openviking.core.uri_validation import validate_request_viking_uri
from openviking.pyagfs.exceptions import (
    AGFSClientError,
    AGFSNotFoundError,
    GitRestoreWritebackPartialError,
)
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.error_mapping import map_exception
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking_cli.exceptions import (
    InternalError,
    InvalidArgumentError,
    NotFoundError,
    OpenVikingError,
)

router = APIRouter(prefix="/api/v1/snapshot", tags=["snapshot"])

MAX_LOG_FILTER_PATHS = 32


class CommitRequest(BaseModel):
    """Request body for ``POST /api/v1/snapshot/commit``."""

    model_config = ConfigDict(extra="forbid")

    message: str
    paths: Optional[List[str]] = None
    branch: str = "main"
    author_name: Optional[str] = None
    author_email: Optional[str] = None


@router.post("/commit")
async def commit(
    request: CommitRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create a new snapshot of the current workspace state."""
    service = get_service()
    paths = (
        [validate_request_viking_uri(path, _ctx, field_name="paths") for path in request.paths]
        if request.paths is not None
        else None
    )
    try:
        result = await service.fs.commit(
            message=request.message,
            paths=paths,
            branch=request.branch,
            author_name=request.author_name,
            author_email=request.author_email,
            ctx=_ctx,
        )
    except (AGFSClientError, ValueError) as e:
        mapped = map_exception(e)
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.get("/log")
async def log(
    branch: str = Query("main", description="Branch ref name"),
    limit: int = Query(20, ge=1, le=500, description="Max commits to return"),
    paths: Optional[List[str]] = Query(
        None,
        max_length=MAX_LOG_FILTER_PATHS,
        description=(
            "Optional viking:// paths; repeat up to 32 values to filter history "
            "to commits touching any path"
        ),
    ),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Walk commit history newest-first along parents[0]."""
    service = get_service()
    canonical_paths = (
        [validate_request_viking_uri(path, _ctx, field_name="paths") for path in paths]
        if paths is not None
        else None
    )
    try:
        result = await service.fs.log(
            branch=branch, limit=limit, paths=canonical_paths, ctx=_ctx
        )
    except AGFSNotFoundError:
        raise NotFoundError(branch, "git_ref")
    except (AGFSClientError, ValueError) as e:
        mapped = map_exception(e)
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


class RestoreRequest(BaseModel):
    """Request body for ``POST /api/v1/snapshot/restore``."""

    model_config = ConfigDict(extra="forbid")

    project_dir: Optional[str] = None
    source_commit: str
    branch: str = "main"
    dry_run: bool = False
    message: Optional[str] = None
    author_name: Optional[str] = None
    author_email: Optional[str] = None


@router.post("/restore")
async def restore(
    request: RestoreRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Forward-commit restore: rebuild project_dir from source_commit on top of HEAD."""
    service = get_service()
    project_dir = (
        validate_request_viking_uri(request.project_dir, _ctx, field_name="project_dir")
        if request.project_dir
        else None
    )
    try:
        result = await service.fs.restore(
            project_dir=project_dir,
            source_commit=request.source_commit,
            branch=request.branch,
            dry_run=request.dry_run,
            message=request.message,
            author_name=request.author_name,
            author_email=request.author_email,
            ctx=_ctx,
        )
    except AGFSNotFoundError as e:
        raise NotFoundError(request.source_commit, "git_ref") from e
    except GitRestoreWritebackPartialError as exc:
        # HEAD already advanced to the new commit, but some per-path VFS
        # writes/deletes failed. Surface structured diagnostics (including
        # task_id of the scheduled reindex) instead of collapsing to a
        # generic InternalError.
        raise OpenVikingError(
            f"snapshot restore partial: {exc}",
            code="RESTORE_WRITEBACK_PARTIAL",
            details=exc.to_dict(),
        ) from exc
    except (AGFSClientError, ValueError) as e:
        mapped = map_exception(e)
        if mapped is not None:
            raise mapped from e
        raise
    except RuntimeError as e:
        # Fallback for the case where the native git binding cannot import
        # pyagfs and surfaces apply-phase failures as a bare RuntimeError.
        # With GitRestoreWritebackPartialError wired up, structured partial
        # failures now go through the branch above; this clause only catches
        # the degraded path.
        raise InternalError(
            f"snapshot restore failed: {e}", cause=e
        ) from e
    return Response(status="ok", result=result)


@router.get("/show")
async def show(
    target_ref: str = Query(..., description="Commit oid, branch, or tag"),
    path: Optional[str] = Query(None, description="Optional viking:// URI for a single blob"),
    raw: bool = Query(True, description="Return raw stored content with memory fields"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Without ``path``: commit metadata JSON. With ``path``: raw blob bytes + X-Snapshot-* headers."""
    service = get_service()
    canonical_path = (
        validate_request_viking_uri(path, _ctx, field_name="path") if path is not None else None
    )
    try:
        if canonical_path is None:
            result = await service.fs.show(target_ref, ctx=_ctx)
            return Response(status="ok", result=result)

        blob = await service.fs.show_blob_raw(target_ref, path=canonical_path, ctx=_ctx)
    except AGFSNotFoundError as e:
        resource = canonical_path if canonical_path is not None else target_ref
        raise NotFoundError(
            resource, "git_blob" if canonical_path is not None else "git_ref"
        ) from e
    except (AGFSClientError, ValueError) as e:
        mapped = map_exception(e)
        if mapped is not None:
            raise mapped from e
        raise

    content = blob["bytes"]
    if not raw:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidArgumentError("raw=false requires UTF-8 snapshot content") from exc

        from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

        content = MemoryFileUtils.read(text, uri=canonical_path).content.encode("utf-8")

    return FastAPIResponse(
        content=content,
        media_type="application/octet-stream",
        headers={
            "X-Snapshot-Oid": str(blob["oid"]),
            "X-Snapshot-Size": str(len(content)),
        },
    )


@router.get("/diff")
async def diff(
    path: str = Query(..., description="Viking URI of the file to compare"),
    from_ref: Optional[str] = Query(
        None,
        alias="from",
        description="Base commit oid, branch, or tag; omit to compare against an empty file",
    ),
    to_ref: str = Query(..., alias="to", description="Target commit oid, branch, or tag"),
    raw: bool = Query(True, description="Compare raw stored content with memory fields"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Return a unified text diff for one path between two snapshots."""
    service = get_service()
    path = validate_request_viking_uri(path, _ctx, field_name="path")
    try:
        result = await service.fs.diff(
            path=path,
            from_ref=from_ref,
            to_ref=to_ref,
            raw=raw,
            ctx=_ctx,
        )
    except (AGFSClientError, ValueError) as exc:
        mapped = map_exception(exc)
        if mapped is not None:
            raise mapped from exc
        raise
    return Response(status="ok", result=result)


@router.get("/ignore")
async def get_ignore(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Return the account ``.ovgitignore`` content (empty string if absent)."""
    service = get_service()
    try:
        result = await service.fs.get_gitignore(ctx=_ctx)
    except AGFSClientError as e:
        mapped = map_exception(e)
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


class SetIgnoreRequest(BaseModel):
    """Request body for ``PUT /api/v1/snapshot/ignore``."""

    model_config = ConfigDict(extra="forbid")

    content: str


@router.put("/ignore")
async def set_ignore(
    request: SetIgnoreRequest = Body(...),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Write the account ``.ovgitignore`` control file."""
    service = get_service()
    try:
        await service.fs.set_gitignore(content=request.content, ctx=_ctx)
    except AGFSClientError as e:
        mapped = map_exception(e)
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=None)


@router.delete("/ignore")
async def delete_ignore(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Delete the account ``.ovgitignore`` control file (missing is success)."""
    service = get_service()
    try:
        await service.fs.delete_gitignore(ctx=_ctx)
    except AGFSClientError as e:
        mapped = map_exception(e)
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=None)
