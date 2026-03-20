# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Prometheus metrics endpoint for OpenViking HTTP Server."""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(request: Request):
    """Return Prometheus metrics in text exposition format."""
    observer = getattr(request.app.state, "prometheus_observer", None)
    if observer is None:
        return PlainTextResponse(status_code=404, content="Prometheus metrics are disabled.\n")

    return PlainTextResponse(
        content=observer.render_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
