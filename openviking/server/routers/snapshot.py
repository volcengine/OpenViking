# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP routes for git-style version control (snapshots).

Mirrors VikingFS.commit / VikingFS.restore / VikingFS.show / VikingFS.log,
which already implement the underlying semantics.
"""

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict

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
from openviking_cli.exceptions import InternalError, NotFoundError, OpenVikingError

router = APIRouter(prefix="/api/v1/snapshot", tags=["snapshot"])


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
    try:
        result = await service.fs.commit(
            message=request.message,
            paths=request.paths,
            branch=request.branch,
            author_name=request.author_name,
            author_email=request.author_email,
            ctx=_ctx,
        )
    except AGFSClientError as e:
        mapped = map_exception(e)
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.get("/log")
async def log(
    branch: str = Query("main", description="Branch ref name"),
    limit: int = Query(20, ge=1, le=500, description="Max commits to return"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Walk commit history newest-first along parents[0]."""
    service = get_service()
    try:
        result = await service.fs.log(branch=branch, limit=limit, ctx=_ctx)
    except AGFSNotFoundError:
        raise NotFoundError(branch, "git_ref")
    except AGFSClientError as e:
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
    try:
        result = await service.fs.restore(
            project_dir=request.project_dir,
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
    except AGFSClientError as e:
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
    _ctx: RequestContext = Depends(get_request_context),
):
    """Without ``path``: commit metadata JSON. With ``path``: raw blob bytes + X-Snapshot-* headers."""
    service = get_service()
    try:
        if path is None:
            result = await service.fs.show(target_ref, ctx=_ctx)
            return Response(status="ok", result=result)

        blob = await service.fs.show_blob_raw(target_ref, path=path, ctx=_ctx)
    except AGFSNotFoundError as e:
        resource = path if path is not None else target_ref
        raise NotFoundError(resource, "git_blob" if path is not None else "git_ref") from e
    except AGFSClientError as e:
        mapped = map_exception(e)
        if mapped is not None:
            raise mapped from e
        raise

    return FastAPIResponse(
        content=blob["bytes"],
        media_type="application/octet-stream",
        headers={
            "X-Snapshot-Oid": str(blob["oid"]),
            "X-Snapshot-Size": str(blob["size"]),
        },
    )
