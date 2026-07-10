# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared helper to ingest an already-uploaded temp file as a resource.

Used by both the MCP ``add_resource`` tool (``temp_file_id`` branch) and the signed
``temp_upload`` route (automatic post-upload ingestion). Resolves the temp file, calls
``ResourceService.add_resource`` (async, ``wait=False``), and drives the ``TempUploadStore``
lifecycle: mark_consumed on success / mark_failed on error, always cleaning up.
"""

from __future__ import annotations

from typing import Any, Optional

from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.temp_upload_store import TempUploadStore


async def ingest_temp_upload(
    store: TempUploadStore,
    temp_file_id: str,
    ctx: RequestContext,
    *,
    to: str = "",
    reason: str = "",
    args: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve a temp upload and ingest it as a resource; return the raw add_resource result.

    The return value is the service's own dict — either a success payload (containing
    ``root_uri``) or a business-error dict (``{"status": "error", ...}``) that ``add_resource``
    returns WITHOUT raising. Callers MUST inspect ``status``: HTTP callers pass it through
    ``response_from_result`` (which maps errors to the right status code); the MCP tool formats
    it — so an ingestion failure is never reported as success. The upload is marked consumed
    only on success (mark_failed on business error or exception), and its temp file is always
    cleaned up. ``resolve_for_consume`` may raise (PermissionDenied / InvalidArgument) before
    anything is resolved — the caller surfaces that.
    """
    resolved = await store.resolve_for_consume(temp_file_id, ctx)
    try:
        try:
            result = await get_service().resources.add_resource(
                path=resolved.local_path,
                ctx=ctx,
                to=to or None,
                reason=reason,
                source_name=resolved.original_filename,
                wait=False,
                allow_local_path_resolution=True,
                enforce_public_remote_targets=True,
                args=args,
            )
        except Exception:
            await store.mark_failed(resolved, ctx)
            raise
        if isinstance(result, dict) and result.get("status") == "error":
            await store.mark_failed(resolved, ctx)
        else:
            await store.mark_consumed(resolved, ctx)
    finally:
        await resolved.cleanup()

    return result
