# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""System endpoints for OpenViking HTTP Server."""

import asyncio
import os
import signal
from typing import Optional

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openviking.server.auth import get_request_context, require_role, resolve_identity
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ErrorInfo, Response
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

router = APIRouter()
_RESTART_DELAY_SECONDS = 0.2


def _terminate_process(pid: int, sig: signal.Signals = signal.SIGTERM) -> None:
    os.kill(pid, sig)


async def _delayed_terminate(delay_seconds: float = _RESTART_DELAY_SECONDS) -> None:
    await asyncio.sleep(delay_seconds)
    _terminate_process(os.getpid(), signal.SIGTERM)


def _schedule_restart(delay_seconds: float = _RESTART_DELAY_SECONDS) -> None:
    asyncio.create_task(_delayed_terminate(delay_seconds))


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
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get system status."""
    service = get_service()
    return Response(
        status="ok",
        result={
            "initialized": service._initialized,
            "user": service.user._user_id,
        },
    )


class WaitRequest(BaseModel):
    """Request model for wait."""

    timeout: Optional[float] = None


class RestartRequest(BaseModel):
    """Request model for restart."""

    force: bool = True
    delay_seconds: float = _RESTART_DELAY_SECONDS


def _detect_supervisor() -> dict:
    """Best-effort supervisor detection based on environment hints."""
    details = {
        "kubernetes": bool(os.getenv("KUBERNETES_SERVICE_HOST")),
        "systemd": bool(os.getenv("INVOCATION_ID") or os.getenv("JOURNAL_STREAM")),
        "supervisord": bool(
            os.getenv("SUPERVISOR_ENABLED") or os.getenv("SUPERVISOR_PROCESS_NAME")
        ),
    }
    details["detected"] = any(details.values())
    return details


@router.post("/api/v1/system/wait", tags=["system"])
async def wait_processed(
    request: WaitRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Wait for all processing to complete."""
    service = get_service()
    result = await service.resources.wait_processed(timeout=request.timeout)
    return Response(status="ok", result=result)


@router.post("/api/v1/system/restart", tags=["system"])
async def restart_server(
    request: Optional[RestartRequest] = Body(default=None),
    _ctx: RequestContext = require_role(Role.ROOT),
):
    payload = request or RestartRequest()
    supervisor = _detect_supervisor()

    if not supervisor["detected"] and not payload.force:
        return JSONResponse(
            status_code=412,
            content=Response(
                status="error",
                error=ErrorInfo(
                    code="FAILED_PRECONDITION",
                    message=(
                        "No external supervisor detected. Restart may stop the service. "
                        "Set force=true to terminate anyway."
                    ),
                    details={"supervisor": supervisor},
                ),
            ).model_dump(),
        )

    _schedule_restart(payload.delay_seconds)

    message = "Restart scheduled. Service may be briefly unavailable."
    if not supervisor["detected"]:
        message = (
            "Termination scheduled, but no external supervisor detected. "
            "Service may not come back automatically."
        )

    return Response(
        status="ok",
        result={
            "message": message,
            "action": "terminate",
            "restart_requires_supervisor": True,
            "supervisor": supervisor,
            "delay_seconds": payload.delay_seconds,
        },
    )
