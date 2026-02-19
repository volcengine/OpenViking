# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Observer endpoints for OpenViking HTTP Server.

Provides observability API for monitoring component status.
Mirrors the SDK's client.observer API:
- /api/v1/observer/queue - Queue status
- /api/v1/observer/vikingdb - VikingDB status
- /api/v1/observer/vlm - VLM status
- /api/v1/observer/system - System overall status
"""

from fastapi import APIRouter, Depends

from openviking.server.auth import verify_api_key
from openviking.server.dependencies import get_service
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
    _: bool = Depends(verify_api_key),
):
    """Get queue system status."""
    service = get_service()
    component = service.debug.observer.queue
    return Response(status="ok", result=_component_to_dict(component))


@router.get("/vikingdb")
async def observer_vikingdb(
    _: bool = Depends(verify_api_key),
):
    """Get VikingDB status."""
    service = get_service()
    component = service.debug.observer.vikingdb
    return Response(status="ok", result=_component_to_dict(component))


@router.get("/vlm")
async def observer_vlm(
    _: bool = Depends(verify_api_key),
):
    """Get VLM (Vision Language Model) token usage status."""
    service = get_service()
    component = service.debug.observer.vlm
    return Response(status="ok", result=_component_to_dict(component))


@router.get("/transaction")
async def observer_transaction(
    _: bool = Depends(verify_api_key),
):
    """Get transaction system status."""
    service = get_service()
    component = service.debug.observer.transaction
    return Response(status="ok", result=_component_to_dict(component))


@router.get("/system")
async def observer_system(
    _: bool = Depends(verify_api_key),
):
    """Get system overall status (includes all components)."""
    service = get_service()
    status = service.debug.observer.system
    return Response(status="ok", result=_system_to_dict(status))
