# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""System endpoints for OpenViking HTTP Server."""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_viking_uri
from openviking.pyagfs.exceptions import AGFSInvalidOperationError, AGFSNotSupportedError
from openviking.server.auth import get_request_context, require_role, resolve_identity
from openviking.server.dependencies import get_service
from openviking.server.identity import AuthMode, RequestContext, Role
from openviking.server.models import Response
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _is_ready_check_ok(value) -> bool:
    """Return whether one readiness check value represents a healthy state."""
    if isinstance(value, dict):
        status = value.get("status")
        if status not in ("ok", "not_configured", "not_supported"):
            return False
        nested = value.get("checks")
        if nested is None:
            return True
        return all(_is_ready_check_ok(item) for item in nested.values())
    return value in ("ok", "not_configured", "not_supported")


async def _probe_agfs_readiness() -> dict[str, object]:
    """Return structured AGFS readiness, including multi-write sync health when available."""
    viking_fs = get_viking_fs()
    checks: dict[str, object] = {}

    await viking_fs.ls("viking://", ctx=None)
    checks["filesystem"] = "ok"

    try:
        await viking_fs.system_sync_status("viking://", ctx=None)
        checks["multiwrite_sync"] = "ok"
    except (AGFSInvalidOperationError, AGFSNotSupportedError):
        checks["multiwrite_sync"] = "not_supported"

    return {"status": "ok", "checks": checks}


async def _embedding_probe(embedder) -> str:
    """Quick embedding probe: embed a single token and check for errors."""
    from openviking.models.embedder.base import embed_compat

    try:
        await embed_compat(embedder, "ok", is_query=True)
        return "ok"
    except Exception as e:
        provider = getattr(embedder, "provider", "unknown")
        model = getattr(embedder, "model_name", "unknown")
        return f"error: provider={provider} model={model}: {e}"


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
                )
                result["account_id"] = str(identity.account_id)
                result["user_id"] = str(identity.user_id)
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

    # 1. AGFS: probe filesystem access and multi-write sync health
    try:
        checks["agfs"] = await _probe_agfs_readiness()
    except Exception as e:
        checks["agfs"] = {"status": "error", "checks": {"filesystem": f"error: {e}"}}

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

    # 4. Embedding: quick probe to verify the provider is reachable
    try:
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton

        ov_config = OpenVikingConfigSingleton.get_instance()
        embedder = ov_config.embedding.get_embedder()
        if embedder is not None:
            probe_result = await asyncio.wait_for(_embedding_probe(embedder), timeout=10.0)
            checks["embedding"] = probe_result
        else:
            checks["embedding"] = "not_configured"
    except asyncio.TimeoutError:
        checks["embedding"] = "error: probe timed out (provider unreachable)"
    except Exception as e:
        checks["embedding"] = f"error: {e}"

    # 5. Ollama: connectivity check if configured
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

    all_ok = all(_is_ready_check_ok(v) for v in checks.values())
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


class BackendSyncRequest(BaseModel):
    """Request model for backend sync status and retry operations."""

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


@router.post("/api/v1/system/backend/sync-status", tags=["system"])
async def backend_sync_status(
    request: BackendSyncRequest,
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Return multi-write backend sync status for a Viking URI subtree."""
    service = get_service()
    uri = validate_viking_uri(resolve_path_variables(request.uri))
    result = await service.fs.system_sync_status(uri, ctx=ctx)
    return Response(status="ok", result=result)


@router.post("/api/v1/system/backend/sync-retry", tags=["system"])
async def backend_sync_retry(
    request: BackendSyncRequest,
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Retry pending multi-write backend sync work for a Viking URI subtree."""
    service = get_service()
    uri = validate_viking_uri(resolve_path_variables(request.uri))
    result = await service.fs.system_sync_retry(uri, ctx=ctx)
    return Response(status="ok", result=result)


@router.get("/api/v1/system/sync/{sync_path:path}", tags=["system"])
async def admin_sync_status(
    sync_path: str,
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Return multi-write backend sync status for one URI subtree through the admin API."""
    service = get_service()
    uri = validate_viking_uri(resolve_path_variables(sync_path))
    result = await service.fs.system_sync_status(uri, ctx=ctx)
    return Response(status="ok", result=result)


@router.post("/api/v1/system/sync/{sync_path:path}/retry", tags=["system"])
async def admin_sync_retry(
    sync_path: str,
    ctx: RequestContext = require_role(Role.ROOT, Role.ADMIN),
):
    """Retry pending multi-write backend sync work for one URI subtree through the admin API."""
    service = get_service()
    uri = validate_viking_uri(resolve_path_variables(sync_path))
    result = await service.fs.system_sync_retry(uri, ctx=ctx)
    return Response(status="ok", result=result)
