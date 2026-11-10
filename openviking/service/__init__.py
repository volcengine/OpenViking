# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Service layer for OpenViking.

Provides business logic decoupled from transport layer,
enabling reuse across HTTP Server and CLI.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openviking.service.core import OpenVikingService
    from openviking.service.debug_service import ComponentStatus, DebugService, SystemStatus
    from openviking.service.fs_service import FSService
    from openviking.service.pack_service import PackService
    from openviking.service.resource_service import ResourceService
    from openviking.service.search_service import SearchService
    from openviking.service.session_service import SessionService

_EXPORTS = {
    "OpenVikingService": ("openviking.service.core", "OpenVikingService"),
    "ComponentStatus": ("openviking.service.debug_service", "ComponentStatus"),
    "DebugService": ("openviking.service.debug_service", "DebugService"),
    "SystemStatus": ("openviking.service.debug_service", "SystemStatus"),
    "FSService": ("openviking.service.fs_service", "FSService"),
    "PackService": ("openviking.service.pack_service", "PackService"),
    "ResourceService": ("openviking.service.resource_service", "ResourceService"),
    "SearchService": ("openviking.service.search_service", "SearchService"),
    "SessionService": ("openviking.service.session_service", "SessionService"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(__all__))


__all__ = [
    "OpenVikingService",
    "ComponentStatus",
    "DebugService",
    "SystemStatus",
    "FSService",
    "PackService",
    "SearchService",
    "ResourceService",
    "SessionService",
]
