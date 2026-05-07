# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Prometheus metrics endpoint for OpenViking HTTP Server."""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from openviking.metrics.exporters.prometheus import PrometheusExporter

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(request: Request):
    """Return Prometheus metrics in text exposition format."""
    exporters = getattr(request.app.state, "metrics_exporters", [])
    # Find PrometheusExporter from the list
    prometheus_exporter = next(
        (e for e in exporters if isinstance(e, PrometheusExporter)),
        None,
    )
    if prometheus_exporter is None:
        return PlainTextResponse(status_code=404, content="Prometheus metrics are disabled.\n")

    return PlainTextResponse(
        content=await prometheus_exporter.export(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
