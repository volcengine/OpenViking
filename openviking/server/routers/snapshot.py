# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Snapshot endpoints for OpenViking HTTP Server."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from openviking.core.path_variables import resolve_path_variables
from openviking.pyagfs.exceptions import AGFSClientError, AGFSNotFoundError
from openviking.server.auth import get_request_context, require_auth_root_or_admin
from openviking.server.dependencies import get_service
from openviking.server.error_mapping import map_exception
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking_cli.exceptions import NotFoundError

router = APIRouter(prefix="/api/v1/snapshot", tags=["snapshot"])


class CreateSnapshotRequest(BaseModel):
    """Request model for creating a snapshot."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    message: str = ""
    wait: bool = True
    timeout: Optional[float] = None
    include_vectors: bool = True


class RestoreSnapshotRequest(BaseModel):
    """Request model for restoring a snapshot."""

    model_config = ConfigDict(extra="forbid")

    uri: str
    create_new: bool = False
    reindex: bool = True
    wait: bool = True
    timeout: Optional[float] = None


@router.post("/create")
@require_auth_root_or_admin
async def create_snapshot(
    body: CreateSnapshotRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Create a snapshot of a directory."""
    service = get_service()
    uri = resolve_path_variables(body.uri)
    try:
        result = await service.snapshot.create(
            uri=uri,
            message=body.message,
            wait=body.wait,
            timeout=body.timeout,
            include_vectors=body.include_vectors,
        )
    except AGFSNotFoundError:
        raise NotFoundError(uri, "resource")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=uri, resource_type="resource")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.get("/get")
@require_auth_root_or_admin
async def get_snapshot(
    uri: str = Query(..., description="Directory URI"),
    commit_id: str = Query(..., description="Commit ID (full or short)"),
    include_files: bool = Query(True, description="Whether to include file list"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get snapshot details."""
    service = get_service()
    uri = resolve_path_variables(uri)
    try:
        result = await service.snapshot.get(
            uri=uri,
            commit_id=commit_id,
            include_files=include_files,
        )
    except AGFSNotFoundError:
        raise NotFoundError(commit_id, "snapshot")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=commit_id, resource_type="snapshot")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.post("/restore")
@require_auth_root_or_admin
async def restore_snapshot(
    commit_id: str = Query(..., description="Target commit ID"),
    body: RestoreSnapshotRequest = ...,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Restore a directory to a snapshot."""
    service = get_service()
    uri = resolve_path_variables(body.uri)
    try:
        result = await service.snapshot.restore(
            uri=uri,
            commit_id=commit_id,
            create_new=body.create_new,
            reindex=body.reindex,
            wait=body.wait,
            timeout=body.timeout,
        )
    except AGFSNotFoundError:
        raise NotFoundError(commit_id, "snapshot")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=commit_id, resource_type="snapshot")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.get("/file")
@require_auth_root_or_admin
async def get_file_at_snapshot(
    uri: str = Query(..., description="Directory URI"),
    file_path: str = Query(..., description="File path relative to uri"),
    commit_id: str = Query(..., description="Commit ID"),
    include_vector: bool = Query(False, description="Whether to include vector data"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get file content at a specific snapshot."""
    service = get_service()
    uri = resolve_path_variables(uri)
    try:
        result = await service.snapshot.get_file(
            uri=uri,
            file_path=file_path,
            commit_id=commit_id,
            include_vector=include_vector,
        )
    except AGFSNotFoundError:
        raise NotFoundError(f"{commit_id}:{file_path}", "file")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=file_path, resource_type="file")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)


@router.post("/copy")
@require_auth_root_or_admin
async def copy_snapshot_to(
    commit_id: str = Query(..., description="Source commit ID"),
    src_uri: str = Query(..., description="Source directory URI (relative to repo root)"),
    dest_uri: str = Query(..., description="Destination directory URI"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """
    Copy the contents of a directory at a specific snapshot to a new directory.

    Equivalent to checking out a specific version of a directory to a new location
    without modifying the original.
    """
    service = get_service()
    src_uri = resolve_path_variables(src_uri)
    dest_uri = resolve_path_variables(dest_uri)
    try:
        result = await service.snapshot.copy_to(
            src_uri=src_uri,
            dest_uri=dest_uri,
            commit_id=commit_id,
        )
    except AGFSNotFoundError:
        raise NotFoundError(commit_id, "snapshot")
    except AGFSClientError as e:
        mapped = map_exception(e, resource=src_uri, resource_type="resource")
        if mapped is not None:
            raise mapped from e
        raise
    return Response(status="ok", result=result)
