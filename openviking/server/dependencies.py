# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Dependency injection for OpenViking HTTP Server."""

from typing import Optional

from openviking.service.core import OpenVikingService

_service: Optional[OpenVikingService] = None


def get_service() -> OpenVikingService:
    """Get the OpenVikingService instance.

    Returns:
        OpenVikingService instance

    Raises:
        RuntimeError: If service is not initialized
    """
    if _service is None:
        raise RuntimeError("OpenVikingService not initialized")
    return _service


def set_service(service: OpenVikingService) -> None:
    """Set the OpenVikingService instance.

    Args:
        service: OpenVikingService instance to set
    """
    global _service
    _service = service
