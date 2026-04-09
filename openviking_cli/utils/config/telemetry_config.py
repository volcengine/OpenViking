# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from pydantic import BaseModel, Field


class TracerConfig(BaseModel):
    """OpenTelemetry tracer configuration."""

    enabled: bool = Field(default=False, description="Enable OpenTelemetry tracing")
    endpoint: str = Field(default="", description="OTLP gRPC endpoint")
    service_name: str = Field(default="openviking", description="Service name for tracing")
    topic: str = Field(default="", description="Trace topic")
    ak: str = Field(default="", description="Access key")
    sk: str = Field(default="", description="Secret key")

    model_config = {"extra": "forbid"}


class TelemetryConfig(BaseModel):
    """Telemetry configuration including tracer."""

    tracer: TracerConfig = Field(
        default_factory=lambda: TracerConfig(), description="OpenTelemetry tracer configuration"
    )

    model_config = {"extra": "forbid"}
