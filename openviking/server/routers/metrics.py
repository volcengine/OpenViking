# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Prometheus /metrics endpoint for OpenViking HTTP Server.

Exposes metrics in Prometheus text exposition format. Opt-in via the
``telemetry.prometheus.enabled`` config flag in ov.conf.
"""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.get("/metrics", tags=["telemetry"])
async def prometheus_metrics(request: Request) -> PlainTextResponse:
    """Serve metrics in Prometheus text exposition format.

    No authentication required (designed for Prometheus scraping).
    """
    prometheus_observer = getattr(request.app.state, "prometheus_observer", None)
    if prometheus_observer is None:
        return PlainTextResponse(
            content="# Prometheus metrics exporter is not enabled.\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    content = prometheus_observer.render_metrics()
    return PlainTextResponse(
        content=content,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
