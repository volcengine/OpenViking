# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Dependency injection for OpenViking HTTP Server."""

from typing import Any

_service: Any | None = None
_server_config: Any | None = None


def get_service() -> Any:
    """Get the OpenVikingService instance.

    Returns:
        OpenVikingService instance

    Raises:
        RuntimeError: If service is not initialized
    """
    if _service is None:
        raise RuntimeError("OpenVikingService not initialized")
    return _service


def get_service_or_none() -> Any | None:
    """Return the registered OpenVikingService, or None if not set."""
    return _service


def set_service(service: Any | None) -> None:
    """Set (or clear) the OpenVikingService instance.

    Args:
        service: OpenVikingService instance to register, or None to clear.
    """
    global _service
    _service = service


def get_server_config() -> Any | None:
    """Return the active ServerConfig if one was registered, else None.

    MCP tools that need server-level settings (e.g. public_base_url for upload URLs)
    use this to read the loaded ServerConfig without going through ``app.state``.
    """
    return _server_config


def set_server_config(config: Any) -> None:
    """Register the active ServerConfig at server bootstrap."""
    global _server_config
    _server_config = config
