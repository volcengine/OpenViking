# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the bot-proxy endpoints (non-streaming).

These models mirror the upstream Vikingbot HTTP channel response types
defined in ``bot/vikingbot/channels/openapi_models.py``. They are kept as
*independent* mirrors rather than direct imports because:

- The ``bot`` extra is optional and may not be installed alongside the
  server (see ``pyproject.toml``).
- The server and bot services can be deployed to separate processes /
  hosts; cross-package typing would be a deployment coupling regression.

Any breaking change to the upstream schema must be reflected here. The
``extra='allow'`` config forwards unknown fields so an upstream revision
that adds a field does not cause silent drop at the proxy layer.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BotHealthResponse(BaseModel):
    """Mirror of ``bot.vikingbot.channels.openapi_models.HealthResponse``."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(default="healthy")
    version: Optional[str] = None
    # ISO 8601 datetime string as emitted by Pydantic; left permissive
    # because upstream serializes via datetime which lands as a string in
    # JSON bodies but may be ``None`` in error scenarios.
    timestamp: Optional[str] = None


class BotChatResponse(BaseModel):
    """Mirror of ``bot.vikingbot.channels.openapi_models.ChatResponse``."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    message: str
    events: Optional[List[Dict[str, Any]]] = None
    timestamp: Optional[str] = None
