# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Observer endpoints for OpenViking HTTP Server.

Provides observability API for monitoring component status.
Mirrors SDK's client.observer API:
- /api/v1/observer/queue - Queue status
- /api/v1/observer/vikingdb - VikingDB status
- /api/v1/observer/models - Models status (VLM, Embedding, Rerank)
- /api/v1/observer/system - System overall status
"""

from fastapi import APIRouter, Depends

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.service.debug_service import ComponentStatus, SystemStatus

router = APIRouter(prefix="/api/v1/observer", tags=["observer"])


def _component_to_dict(component: ComponentStatus) -> dict:
    """Convert ComponentStatus to dict."""
    return {
        "name": component.name,
        "is_healthy": component.is_healthy,
        "has_errors": component.has_errors,
        "status": component.status,
    }


def _system_to_dict(status: SystemStatus) -> dict:
    """Convert SystemStatus to dict."""
    return {
        "is_healthy": status.is_healthy,
        "errors": status.errors,
        "components": {
            name: _component_to_dict(component) for name, component in status.components.items()
        },
    }


@router.get("/queue")
async def observer_queue(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get queue system status."""
    service = get_service()
    component = service.debug.observer.queue
    return Response(status="ok", result=_component_to_dict(component))


@router.get("/vikingdb")
async def observer_vikingdb(
    ctx: RequestContext = Depends(get_request_context),
):
    """Get VikingDB status."""
    service = get_service()
    component = service.debug.observer.vikingdb(ctx=ctx)
    return Response(status="ok", result=_component_to_dict(component))


@router.get("/models")
async def observer_models(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get models status (VLM, Embedding, Rerank)."""
    service = get_service()
    component = service.debug.observer.models
    return Response(status="ok", result=_component_to_dict(component))


@router.get("/lock")
async def observer_lock(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get lock system status."""
    service = get_service()
    component = service.debug.observer.lock
    return Response(status="ok", result=_component_to_dict(component))


@router.get("/retrieval")
async def observer_retrieval(
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get retrieval quality metrics."""
    service = get_service()
    component = service.debug.observer.retrieval
    return Response(status="ok", result=_component_to_dict(component))


@router.get("/system")
async def observer_system(
    ctx: RequestContext = Depends(get_request_context),
):
    """Get system overall status (includes all components)."""
    service = get_service()
    status = service.debug.observer.system(ctx=ctx)
    return Response(status="ok", result=_system_to_dict(status))
