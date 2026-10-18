# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Standalone host: startup checks, Studio, read-only OV administration and /health."""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import quote, urlencode

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from .app import create_app
from .client import VikingClient, VikingError
from .config import MOUNT_PATH, StandaloneGatewayConfig
from .management import request_credential

logger = logging.getLogger(__name__)
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
WARNINGS = {
    "public_openviking_without_management_key": "This gateway is open to every OpenViking "
    "Cloud user. Set OPENVIKING_GATEWAY_MANAGEMENT_KEY to restrict changes to upstreams and "
    "profiles.",
}


async def check_openviking(config: StandaloneGatewayConfig) -> dict:
    """Probe OV once before serving; refuse trusted auth and unconfirmed public binds."""
    async with aiohttp.ClientSession(trust_env=False) as http:
        viking = VikingClient(http, config.openviking_url, config.min_server_version)
        try:
            health = await viking.health(require_identity=False)
        except VikingError as error:
            health = {"status": "degraded", "reason": error.reason}
    if health.get("reason") == "trusted_mode_unsupported":
        raise ValueError("OpenViking Gateway does not support trusted authentication")
    if config.host not in LOOPBACK_HOSTS and health.get("auth_mode") != "api_key":
        raise ValueError(
            "A publicly bound OpenViking Gateway requires a successful synchronous "
            "probe confirming API-key authentication; use a loopback host until "
            "OpenViking is reachable and its authentication mode is known"
        )
    return health


def studio_directory() -> Path | None:
    if path := os.environ.get("OPENVIKING_WEB_STUDIO_DIR"):
        return Path(path).expanduser()
    spec = find_spec("openviking")
    if spec and spec.submodule_search_locations:
        path = Path(next(iter(spec.submodule_search_locations))) / "web_studio" / "dist"
        if path.is_dir():
            return path
    return None


def build_encryptor(config: StandaloneGatewayConfig):
    """OpenViking's encryptor for the optional ``encryption`` block; None stores plaintext."""
    if not (config.encryption or {}).get("enabled"):
        return None
    from openviking.crypto.config import bootstrap_encryption

    # uvicorn builds the app inside its running loop, so bootstrap on a separate thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            asyncio.run, bootstrap_encryption({"encryption": config.encryption})
        ).result()


def create_standalone_app(config: StandaloneGatewayConfig, studio_dir=None):
    """Mount the gateway at ``MOUNT_PATH``; Studio, /health and the OV proxy stay at the root."""
    gateway = create_app(
        config, openviking_url=config.openviking_url, encryptor=build_encryptor(config)
    )
    for warning in gateway.state.warnings:
        logger.warning("%s: %s", warning, WARNINGS[warning])
    # A mounted app's lifespan does not run on its own; its error shapes apply here too.
    app = FastAPI(
        lifespan=lambda _app: gateway.router.lifespan_context(gateway),
        exception_handlers=gateway.exception_handlers,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    state = gateway.state

    @app.get("/health")
    async def health(request: Request):
        result = {
            "status": "ok",
            "service": "openviking-gateway",
            "auth_mode": state.health.get("auth_mode", "api_key"),
            "openviking_url": state.openviking_url,
            "openviking": state.health,
        }
        if key := request_credential(request):
            # Studio learns of an invalid OV key from /access, not from host detection.
            try:
                identity = await state.viking.identify(key)
            except VikingError:
                identity = {}
            result.update(
                {
                    field: identity[field]
                    for field in ("account_id", "user_id", "role", "auth_mode")
                    if field in identity
                }
            )
        return result

    @app.get("/api/v1/admin/accounts")
    @app.get("/api/v1/admin/accounts/{account_id}/users")
    async def administration(request: Request, account_id: str | None = None):
        credential = request_credential(request)
        # Rejects trusted mode, where OV would authorize identity headers this route drops.
        await state.viking.identify(credential)
        path = "/api/v1/admin/accounts"
        if account_id is not None:
            path += "/" + quote(account_id, safe="") + "/users"
        params = dict(request.query_params)
        if account_id is not None:
            params["include_credentials"] = "false"
        if params:
            path += "?" + urlencode(params)
        result = await state.viking.request("GET", path, credential)
        return {"status": "ok", "result": result}

    directory = studio_dir or studio_directory()
    root = Path(directory).resolve() if directory else None

    @app.get("/")
    async def home():
        return RedirectResponse("/studio/")

    @app.get("/studio")
    async def studio_redirect():
        return RedirectResponse("/studio/")

    @app.get("/studio/{path:path}")
    async def studio(path: str):
        if root is None or not (root / "index.html").is_file():
            raise HTTPException(503, "OpenViking Studio bundle is unavailable")
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(404)
        if candidate.is_file():
            return FileResponse(candidate)
        if path.startswith("assets/"):
            raise HTTPException(404)
        return FileResponse(root / "index.html")

    app.mount(MOUNT_PATH, gateway)
    return app
