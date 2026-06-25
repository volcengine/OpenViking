# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""FastAPI app exposing OpenViking endpoints as OpenWebUI tools."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from .client import OVClient
from .config import Settings, load_settings
from .tools import router as tools_router


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Build the FastAPI app. Tests inject a custom Settings/OVClient."""
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = OVClient(resolved)
        app.state.ov_client = client
        app.state.ov_settings = resolved
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title="OpenViking OpenWebUI Tools",
        version="0.1.0",
        description=(
            "OpenAPI tool server that fronts a curated set of OpenViking "
            "endpoints so OpenWebUI can call them as tools."
        ),
        lifespan=lifespan,
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "endpoint": resolved.endpoint}

    app.include_router(tools_router)
    return app


app = create_app()
