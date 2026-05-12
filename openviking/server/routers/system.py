# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""System endpoints for OpenViking HTTP Server."""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openviking.core.uri_validation import validate_viking_uri
from openviking.server.auth import get_request_context, resolve_identity
from openviking.server.dependencies import get_service
from openviking.server.identity import AuthMode, RequestContext
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

    # Try to get user identity
    try:
        # Extract headers manually
        x_api_key = request.headers.get("X-API-Key")
        authorization = request.headers.get("Authorization")
        x_openviking_account = request.headers.get("X-OpenViking-Account")
        x_openviking_user = request.headers.get("X-OpenViking-User")
        x_openviking_agent = request.headers.get("X-OpenViking-Agent")

        # Get effective auth mode from config
        effective_auth_mode = AuthMode.API_KEY
        config = getattr(request.app.state, "config", None)
        if config is not None and hasattr(config, "get_effective_auth_mode"):
            effective_auth_mode = config.get_effective_auth_mode()
        result["auth_mode"] = effective_auth_mode.value

        if x_api_key or authorization:
            try:
                identity = await resolve_identity(
                    request,
                    x_api_key=x_api_key,
                    authorization=authorization,
                    x_openviking_account=x_openviking_account,
                    x_openviking_user=x_openviking_user,
                    x_openviking_agent=x_openviking_agent,
                )
                result["account_id"] = str(identity.account_id)
                result["user_id"] = str(identity.user_id)
                result["agent_id"] = str(identity.agent_id)
                result["role"] = identity.role.value
            except Exception as e:
                logger.warning(f"Failed to resolve identity: {e}")
    except Exception as e:
        logger.error(f"Failed to get health check: {e}")

    return result


@router.get("/ready", tags=["system"])
async def readiness_check(request: Request):
    """Readiness probe — checks AGFS, VectorDB, and APIKeyManager.

    Returns 200 when all subsystems are operational, 503 otherwise.
    No authentication required (designed for K8s probes).
    """
    # If service is still initializing, return 503 immediately
    try:
        service = get_service()
        if not service._initialized:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "initializing"},
            )
    except RuntimeError:
        # get_service() raises RuntimeError when service not yet set
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "initializing"},
        )

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

    # 4. Ollama: connectivity check if configured
    try:
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
        from openviking_cli.utils.ollama import check_ollama_running, detect_ollama_in_config

        ov_config = OpenVikingConfigSingleton.get_instance()
        uses_ollama, ollama_host, ollama_port = detect_ollama_in_config(ov_config)
        if uses_ollama:
            if check_ollama_running(ollama_host, ollama_port):
                checks["ollama"] = "ok"
            else:
                checks["ollama"] = f"unreachable at {ollama_host}:{ollama_port}"
        else:
            checks["ollama"] = "not_configured"
    except Exception as e:
        checks["ollama"] = f"error: {e}"

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


class ConsistencyRequest(BaseModel):
    """Request model for filesystem/vector-index consistency checks."""

    uri: str


@router.post("/api/v1/system/wait", tags=["system"])
async def wait_processed(
    request: WaitRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Wait for all processing to complete."""
    service = get_service()
    result = await service.resources.wait_processed(timeout=request.timeout)
    return Response(status="ok", result=result)


@router.post("/api/v1/system/consistency", tags=["system"])
async def check_consistency(
    request: ConsistencyRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Check filesystem/vector-index consistency for a URI subtree."""
    service = get_service()
    uri = validate_viking_uri(request.uri)
    result = await service.check_consistency(
        uri=uri,
        ctx=ctx,
    )
    return Response(status="ok", result=result)
