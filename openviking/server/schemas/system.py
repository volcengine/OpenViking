# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /health, /ready, /api/v1/system/* endpoints.

``/health`` and ``/ready`` historically return bare JSON (no ``Response[T]``
envelope) because they are designed for K8s / probe consumers that expect
a simple status payload. The typed schema mirrors this contract directly
instead of wrapping — same reasoning as the bot-proxy endpoints in
``schemas/bot.py``.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class SystemHealthResponse(BaseModel):
    """Payload of ``GET /health`` (no Response envelope)."""

    model_config = ConfigDict(extra="allow")

    status: str = "ok"
    healthy: bool = True
    version: Optional[str] = None
    user_id: Optional[str] = None


class SystemReadyResponse(BaseModel):
    """Payload of ``GET /ready`` (no Response envelope).

    ``checks`` maps subsystem name to either ``"ok"`` / ``"not_configured"``
    / ``"unhealthy"`` or a free-form error string. Kept as ``Dict[str, str]``
    because the subsystem set evolves and free-form error strings are
    opaque.
    """

    model_config = ConfigDict(extra="allow")

    status: str  # "ready" | "not_ready"
    checks: Dict[str, str] = Field(default_factory=dict)


class SystemStatusResult(BaseModel):
    """Result payload of ``GET /api/v1/system/status``."""

    model_config = ConfigDict(extra="allow")

    initialized: bool
    user: str


class WaitProcessedQueueInfo(BaseModel):
    """Per-queue entry inside the ``wait_processed`` dict."""

    model_config = ConfigDict(extra="allow")

    processed: Optional[int] = None
    requeue_count: Optional[int] = None
    error_count: Optional[int] = None
    errors: Optional[list[Dict[str, Any]]] = None


# ``service.resources.wait_processed()`` returns ``{queue_name: QueueInfo}``
# directly (no wrapper object). The result stays a plain dict so the
# queue-name keys flow through transparently.
WaitProcessedResult = Dict[str, WaitProcessedQueueInfo]
