# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""System endpoints for OpenViking HTTP Server."""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openviking.server.auth import get_request_context, resolve_identity
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health", tags=["system"])
async def health_check(request: Request):
    """Health check endpoint (no authentication required)."""
    from openviking import __version__

    result = {"status": "ok", "healthy": True, "version": __version__}

    # Try to get user identity if auth headers are present
    try:
        # Extract headers manually
        x_api_key = request.headers.get("X-API-Key")
        authorization = request.headers.get("Authorization")
        x_openviking_user = request.headers.get("X-OpenViking-User")

        # Check if we have auth or in dev mode
        api_key_manager = getattr(request.app.state, "api_key_manager", None)
        if api_key_manager is None:
            # Dev mode - use default user
            result["user_id"] = x_openviking_user or "default"
        elif x_api_key or authorization:
            # Try to resolve identity
            try:
                identity = await resolve_identity(
                    request,
                    x_api_key=x_api_key,
                    authorization=authorization,
                    x_openviking_account=request.headers.get("X-OpenViking-Account"),
                    x_openviking_user=x_openviking_user,
                    x_openviking_agent=request.headers.get("X-OpenViking-Agent"),
                )
                if identity and identity.user_id:
                    result["user_id"] = identity.user_id
            except Exception:
                pass
    except Exception:
        pass

    return result


@router.get("/ready", tags=["system"])
async def readiness_check(request: Request):
    """Readiness probe — checks AGFS, VectorDB, and APIKeyManager.

    Returns 200 when all subsystems are operational, 503 otherwise.
    No authentication required (designed for K8s probes).
    """
    checks = {}

    # 1. AGFS: try to list root
    try:
        viking_fs = get_viking_fs()
        await viking_fs.ls("viking://", ctx=None)
        checks["agfs"] = "ok"
    except Exception as e:
        checks["agfs"] = f"error: {e}"

    # 2. VectorDB: health_check()
    try:
        viking_fs = get_viking_fs()
        storage = viking_fs._get_vector_store()
        if storage:
            healthy = await storage.health_check()
            checks["vectordb"] = "ok" if healthy else "unhealthy"
        else:
            checks["vectordb"] = "not_configured"
    except Exception as e:
        checks["vectordb"] = f"error: {e}"

    # 3. APIKeyManager: check if loaded
    try:
        manager = getattr(request.app.state, "api_key_manager", None)
        if manager is not None:
            checks["api_key_manager"] = "ok"
        else:
            checks["api_key_manager"] = "not_configured"
    except Exception as e:
        checks["api_key_manager"] = f"error: {e}"

    all_ok = all(v in ("ok", "not_configured") for v in checks.values())
    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if all_ok else "not_ready", "checks": checks},
    )


@router.get("/api/v1/system/status", tags=["system"])
async def system_status(
    ctx: RequestContext = Depends(get_request_context),
):
    """Get system status.

    ``result.user`` is the authenticated request's ``user_id`` (from API key or
    headers), not the process-wide service default — clients use this to resolve
    multi-tenant paths (e.g. OpenClaw plugin).
    """
    service = get_service()
    return Response(
        status="ok",
        result={
            "initialized": service._initialized,
            "user": ctx.user.user_id,
        },
    )


class WaitRequest(BaseModel):
    """Request model for wait."""

    timeout: Optional[float] = None


@router.post("/api/v1/system/wait", tags=["system"])
async def wait_processed(
    request: WaitRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Wait for all processing to complete."""
    service = get_service()
    result = await service.resources.wait_processed(timeout=request.timeout)
    return Response(status="ok", result=result)
