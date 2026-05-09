# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Response models for the /api/v1/config endpoints."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class PrometheusTelemetryConfig(BaseModel):
    """Prometheus sub-section of the server telemetry config."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False


class TelemetryConfig(BaseModel):
    """Top-level telemetry config block."""

    model_config = ConfigDict(extra="allow")

    prometheus: Optional[PrometheusTelemetryConfig] = None


class ServerConfigView(BaseModel):
    """Sanitized ``ServerConfig`` payload returned by GET / PUT /api/v1/config.

    ``root_api_key`` is stripped in the router (``_sanitize_config``) so the
    response never surfaces it. ``extra='allow'`` preserves any ServerConfig
    field added server-side without requiring a schema update — any future
    field flows through as a top-level key in the JSON response.
    """

    model_config = ConfigDict(extra="allow")

    host: Optional[str] = None
    port: Optional[int] = None
    workers: Optional[int] = None
    auth_mode: Optional[str] = None
    cors_origins: Optional[List[str]] = None
    with_bot: Optional[bool] = None
    bot_api_url: Optional[str] = None
    encryption_enabled: Optional[bool] = None
    telemetry: Optional[TelemetryConfig] = None
